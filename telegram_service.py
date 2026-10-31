"""
Telegram upload service.

Handles connecting to Telegram via Pyrogram and uploading video files.
Several files upload in parallel, but messages are always posted in the
original order so episodes stay sorted in the chat.

File bytes go through our own uploader instead of Pyrogram's save_file():
save_file() opens a fresh connection for every file, keeps only 4 parts in
flight on it, and silently drops parts that fail. Here a small pool of media
connections is opened once and shared, each file keeps several parts in
flight spread over all of them, and every part is retried until it lands.
"""

import asyncio
import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger
from pyrogram import Client, raw, utils
from pyrogram.session import Session
from pyrogram.errors import (
    ChannelPrivate,
    ChatAdminRequired,
    ChatRestricted,
    ChatSendMediaForbidden,
    ChatWriteForbidden,
    FilePartMissing,
    FloodWait,
    PeerIdInvalid,
    UserBannedInChannel,
)

import database

try:
    import tgcrypto  # noqa: F401
except ImportError:
    tgcrypto = None

USER_SESSION_NAME = "autoupload_session"

# Errors that will fail for every remaining file too, so the batch stops.
FATAL_SEND_ERRORS = (
    ChatWriteForbidden,
    ChatSendMediaForbidden,
    ChatAdminRequired,
    ChatRestricted,
    ChannelPrivate,
    UserBannedInChannel,
    PeerIdInvalid,
)

PROGRESS_INTERVAL = 0.25  # seconds between progress callbacks per file

# Upload tuning. Throughput on one connection is capped by how much data is
# in flight, so more connections and more parts in flight = faster uploads.
UPLOAD_CONNECTIONS = 4        # media connections shared by all files
PARTS_IN_FLIGHT = 16          # parts of one file being sent at the same time
PART_SIZE = 512 * 1024        # the largest part Telegram accepts
BIG_FILE_THRESHOLD = 10 * 1024 * 1024  # larger files use SaveBigFilePart
PART_TIMEOUT = 60             # seconds; generous so slow links don't time out
PART_RETRIES = 5


@dataclass
class UploadResult:
    """Stores the outcome of a single file upload."""
    filename: str
    success: bool
    error: str = ""
    message_id: int | None = None
    fatal: bool = False  # the same error would hit every remaining file


class ChatResolutionError(Exception):
    """Raised when the configured target chat cannot be resolved."""


class UploadError(Exception):
    """A file could not be uploaded (already logged / user-facing message)."""


class TelegramService:
    """
    Manages the Pyrogram client lifecycle and uploads.

    Usage:
        svc = TelegramService(api_id, api_hash, bot_token=..., parallel_uploads=2)
        await svc.start()
        results = await svc.upload_files(chat_id, file_list, ...)
        await svc.stop()
    """

    def __init__(self, api_id: int, api_hash: str, bot_token: str = "", parallel_uploads: int = 2):
        self._api_id = api_id
        self._api_hash = api_hash
        self._bot_token = bot_token.strip()
        self._parallel = max(1, int(parallel_uploads))
        self._client: Optional[Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._cancel_event: Optional[asyncio.Event] = None
        self._cancel_requested = False
        self._upload_tasks: list[asyncio.Task] = []
        self._media_sessions: list[Session] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start and authenticate the Pyrogram client."""
        if tgcrypto is None:
            logger.warning(
                "TgCrypto is not installed — uploads will be very slow. "
                "Run: pip install TgCrypto-pyrofork"
            )

        self._loop = asyncio.get_running_loop()
        self._cancel_event = asyncio.Event()

        client_kwargs = {
            "api_id": self._api_id,
            "api_hash": self._api_hash,
            "workdir": database.DATA_DIR,
            "sleep_threshold": 60,
        }
        if self._bot_token:
            # One session per bot, so changing the token never reuses the old bot's login.
            bot_id = self._bot_token.split(":", 1)[0]
            client_kwargs["name"] = f"autoupload_bot_{bot_id}"
            client_kwargs["bot_token"] = self._bot_token
        else:
            session_file = os.path.join(database.DATA_DIR, f"{USER_SESSION_NAME}.session")
            has_console = sys.stdin is not None and sys.stdin.isatty()
            if not os.path.exists(session_file) and not has_console:
                raise RuntimeError(
                    "No bot token is set and there is no saved user login. "
                    "Add a Telegram Bot Token in Settings, or run main.py once from a "
                    "terminal to log in with your phone number."
                )
            client_kwargs["name"] = USER_SESSION_NAME

        self._client = Client(**client_kwargs)
        await self._client.start()
        me = await self._client.get_me()
        account_type = "bot" if getattr(me, "is_bot", False) else "user"
        logger.info(f"Logged in as {me.first_name} ({account_type}, ID: {me.id})")

    async def stop(self) -> None:
        """Gracefully disconnect."""
        sessions, self._media_sessions = self._media_sessions, []
        await asyncio.gather(*(s.stop() for s in sessions), return_exceptions=True)
        if self._client and self._client.is_connected:
            await self._client.stop()
            logger.info("Telegram client disconnected.")

    def request_cancel(self) -> None:
        """Stop uploading as soon as possible. Safe to call from any thread."""
        self._cancel_requested = True
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._cancel_inflight)

    def _cancel_inflight(self) -> None:
        if self._cancel_event:
            self._cancel_event.set()
        for task in self._upload_tasks:
            task.cancel()

    # ------------------------------------------------------------------
    # Connection check
    # ------------------------------------------------------------------

    async def check_chat(self, chat_id: str | int) -> str:
        """Resolve the target chat and return a short description of it."""
        resolved_id = await self._resolve_chat_id(chat_id)
        chat = await self._client.get_chat(resolved_id)
        name = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)
        chat_type = getattr(chat.type, "name", str(chat.type)).lower()
        summary = f"{name} ({chat_type}, ID: {chat.id})"

        if chat_type in ("channel", "supergroup", "group"):
            try:
                member = await self._client.get_chat_member(chat.id, "me")
                status = getattr(member.status, "name", str(member.status)).lower()
                summary += f" — you are {status}"
                privileges = getattr(member, "privileges", None)
                if chat_type == "channel" and status not in ("owner", "administrator"):
                    summary += " ⚠ must be an admin to post in a channel"
                elif chat_type == "channel" and privileges and not privileges.can_post_messages:
                    summary += " ⚠ admin is missing 'Post Messages' permission"
            except Exception as e:
                summary += f" — could not read membership: {e}"
        return summary

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_files(
        self,
        chat_id: str | int,
        files: list[dict],
        send_as_video: bool = True,
        delay: int = 2,
        max_retries: int = 3,
        on_progress: Optional[Callable] = None,
        on_file_start: Optional[Callable] = None,
        on_file_done: Optional[Callable] = None,
    ) -> list[UploadResult]:
        """
        Upload *files* to *chat_id*.

        Parameters
        ----------
        files : list[dict]
            Each dict must have keys: path, new_name, index, caption
        delay : int
            Seconds to wait between posted messages. Uploads keep running
            during the wait, so this barely affects total time.
        on_progress : callable(file_info, current_bytes, total_bytes)
        on_file_start : callable(file_info)
        on_file_done : callable(file_info, UploadResult)
        """
        self._cancel_requested = False
        self._cancel_event.clear()
        results: list[UploadResult] = []
        total = len(files)

        resolved_id = await self._resolve_chat_id(chat_id)
        peer = await self._client.resolve_peer(resolved_id)
        await self._ensure_media_sessions()

        semaphore = asyncio.Semaphore(self._parallel)
        # Let uploads run a little ahead of posting, but not so far that
        # Telegram expires the uploaded parts before we send them.
        window = self._parallel * 2
        tasks: dict[int, asyncio.Task] = {}
        next_to_schedule = 0

        def schedule_upto(cursor: int) -> None:
            nonlocal next_to_schedule
            while next_to_schedule < total and next_to_schedule < cursor + window:
                idx = next_to_schedule
                tasks[idx] = asyncio.create_task(
                    self._upload_parts(semaphore, files[idx], max_retries, on_progress, on_file_start)
                )
                next_to_schedule += 1
            self._upload_tasks = list(tasks.values())

        try:
            for i, file_info in enumerate(files):
                schedule_upto(i)
                filename = file_info["new_name"]

                try:
                    input_file = await tasks[i]
                except asyncio.CancelledError:
                    if self._cancel_requested:
                        break
                    raise
                except UploadError as e:
                    result = UploadResult(filename, False, str(e))
                else:
                    if self._cancel_requested:
                        break
                    result = await self._send_with_retry(peer, file_info, input_file, send_as_video, max_retries)

                del tasks[i]
                results.append(result)
                if on_file_done:
                    on_file_done(file_info, result)

                if result.fatal:
                    logger.error("Stopping: the remaining files would fail for the same reason.")
                    break

                if result.success and delay > 0 and i < total - 1:
                    try:
                        await asyncio.wait_for(self._cancel_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass

            if self._cancel_requested:
                logger.warning("Upload cancelled by user.")
        finally:
            leftover = list(tasks.values())
            for t in leftover:
                t.cancel()
            await asyncio.gather(*leftover, return_exceptions=True)
            self._upload_tasks = []

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_chat_id(self, chat_id: str | int) -> int:
        """
        Resolve the target chat before uploading so the app fails once, early,
        with a useful message instead of retrying every file.
        """
        try:
            resolved_chat = await self._client.get_chat(chat_id)
            resolved_id = resolved_chat.id
            chat_name = (
                getattr(resolved_chat, "title", None)
                or getattr(resolved_chat, "first_name", None)
                or str(chat_id)
            )
            logger.info(f"Resolved chat: {chat_name} (ID: {resolved_id})")
            return resolved_id
        except Exception as e:
            message = self._build_chat_resolution_message(chat_id, e)
            logger.error(message)
            raise ChatResolutionError(message) from e

    def _build_chat_resolution_message(self, chat_id: str | int, error: Exception) -> str:
        target = str(chat_id)
        is_numeric = target.lstrip("-").isdigit()

        if self._bot_token and is_numeric:
            return (
                f"Could not resolve chat_id '{target}': {error}. "
                "The bot session cannot access that numeric chat ID yet. "
                "Add the bot to the target group or channel, promote it if needed, "
                "and use the public @username in Settings when one is available."
            )

        if is_numeric:
            return (
                f"Could not resolve chat_id '{target}': {error}. "
                "Verify that the signed-in account is a member of the target chat, "
                "or switch the setting to the chat's @username if it has one."
            )

        return f"Could not resolve chat_id '{target}': {error}"

    async def _ensure_media_sessions(self) -> None:
        """Open the shared upload connections (once per service)."""
        if self._media_sessions:
            return
        client = self._client
        dc_id = await client.storage.dc_id()
        auth_key = await client.storage.auth_key()
        test_mode = await client.storage.test_mode()
        sessions = [
            Session(client, dc_id, auth_key, test_mode, is_media=True)
            for _ in range(UPLOAD_CONNECTIONS)
        ]
        results = await asyncio.gather(*(s.start() for s in sessions), return_exceptions=True)
        self._media_sessions = [s for s, r in zip(sessions, results) if not isinstance(r, BaseException)]
        if not self._media_sessions:
            raise ConnectionError(f"Could not open upload connections: {results[0]}")
        logger.info(f"Opened {len(self._media_sessions)} upload connection(s).")

    async def _upload_parts(
        self,
        semaphore: asyncio.Semaphore,
        file_info: dict,
        max_retries: int,
        on_progress: Optional[Callable],
        on_file_start: Optional[Callable],
    ):
        """Upload the file bytes to Telegram and return the InputFile handle."""
        filepath = file_info["path"]
        filename = file_info["new_name"]

        async with semaphore:
            try:
                size = os.path.getsize(filepath)
            except OSError:
                raise UploadError(f"File not found: {filepath}")
            if size == 0:
                raise UploadError("File is empty")
            limit_mib = 4000 if getattr(self._client.me, "is_premium", False) else 2000
            if size > limit_mib * 1024 * 1024:
                raise UploadError(f"File is larger than {limit_mib} MiB, the limit for this account")

            if on_file_start:
                on_file_start(file_info)

            last_report = 0.0

            def progress(current: int):
                nonlocal last_report
                now = time.monotonic()
                if current < size and now - last_report < PROGRESS_INTERVAL:
                    return
                last_report = now
                if on_progress:
                    on_progress(file_info, current, size)

            attempt = 0
            while True:
                attempt += 1
                try:
                    return await self._save_file(filepath, size, progress)
                except FloodWait as e:
                    logger.warning(f"FloodWait: sleeping {e.value}s before retrying {filename}")
                    await asyncio.sleep(e.value)
                    attempt -= 1
                except (FileNotFoundError, PermissionError, IsADirectoryError) as e:
                    raise UploadError(str(e)) from e
                except Exception as e:
                    logger.error(f"Upload attempt {attempt}/{max_retries} failed for {filename}: {e}")
                    if attempt >= max_retries:
                        raise UploadError(str(e)) from e
                    await asyncio.sleep(3 * attempt)

    async def _save_file(self, filepath: str, size: int, progress: Callable[[int], None]):
        """Send every part of *filepath*, several at once over the shared connections."""
        total_parts = math.ceil(size / PART_SIZE)
        is_big = size > BIG_FILE_THRESHOLD
        file_id = self._client.rnd_id()
        pending = iter(range(total_parts))  # shared by the workers below
        done = 0

        async def worker(n: int):
            nonlocal done
            session = self._media_sessions[n % len(self._media_sessions)]
            with open(filepath, "rb") as fp:
                for part in pending:
                    sent = await self._send_part(session, fp, file_id, part, total_parts, is_big)
                    done += sent  # not "done += await ...", which would read done before awaiting
                    progress(done)

        workers = [asyncio.create_task(worker(n)) for n in range(min(PARTS_IN_FLIGHT, total_parts))]
        try:
            finished, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_EXCEPTION)
            for task in finished:
                if task.exception():
                    raise task.exception()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        name = os.path.basename(filepath)
        if is_big:
            return raw.types.InputFileBig(id=file_id, parts=total_parts, name=name)
        # The MD5 checksum is optional; skipping it lets small files upload in parallel too.
        return raw.types.InputFile(id=file_id, parts=total_parts, name=name, md5_checksum="")

    async def _send_part(self, session: Session, fp, file_id: int, part: int,
                         total_parts: int, is_big: bool) -> int:
        """Read one part (off the event loop) and save it, retrying until it lands."""
        chunk = await asyncio.to_thread(_read_part, fp, part)
        if is_big:
            rpc = raw.functions.upload.SaveBigFilePart(
                file_id=file_id, file_part=part, file_total_parts=total_parts, bytes=chunk,
            )
        else:
            rpc = raw.functions.upload.SaveFilePart(file_id=file_id, file_part=part, bytes=chunk)

        attempt = 0
        while True:
            attempt += 1
            try:
                if not await session.invoke(rpc, timeout=PART_TIMEOUT):
                    raise RuntimeError(f"Telegram rejected part {part}")
                return len(chunk)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                attempt -= 1
            except Exception as e:
                if attempt >= PART_RETRIES:
                    raise
                logger.debug(f"Retrying part {part} ({attempt}/{PART_RETRIES}): {e}")
                await asyncio.sleep(attempt)

    async def _send_with_retry(
        self,
        peer,
        file_info: dict,
        input_file,
        send_as_video: bool,
        max_retries: int,
    ) -> UploadResult:
        """Post the uploaded file as a message, retrying transient errors."""
        filename = file_info["new_name"]
        attempt = 0
        while True:
            attempt += 1
            try:
                message_id = await self._send_media(peer, file_info, input_file, send_as_video)
                logger.success(f"Uploaded: {filename}")
                return UploadResult(filename, True, message_id=message_id)
            except FloodWait as e:
                logger.warning(f"FloodWait: sleeping {e.value}s before posting {filename}")
                await asyncio.sleep(e.value)
                attempt -= 1
            except FATAL_SEND_ERRORS as e:
                return UploadResult(filename, False, str(e), fatal=True)
            except Exception as e:
                logger.error(f"Send attempt {attempt}/{max_retries} failed for {filename}: {e}")
                if attempt >= max_retries:
                    return UploadResult(filename, False, str(e))
                await asyncio.sleep(3 * attempt)

    async def _send_media(self, peer, file_info: dict, input_file, send_as_video: bool) -> int | None:
        client = self._client
        filepath = file_info["path"]
        filename = file_info["new_name"]
        mime_type = client.guess_mime_type(filename) or client.guess_mime_type(filepath)

        attributes = [raw.types.DocumentAttributeFilename(file_name=filename)]
        if send_as_video:
            attributes.insert(0, raw.types.DocumentAttributeVideo(
                supports_streaming=True, duration=0, w=0, h=0,
            ))
            media = raw.types.InputMediaUploadedDocument(
                mime_type=mime_type or "video/mp4", file=input_file, attributes=attributes,
            )
        else:
            media = raw.types.InputMediaUploadedDocument(
                mime_type=mime_type or "application/octet-stream", file=input_file,
                attributes=attributes, force_file=True,
            )

        text = await utils.parse_text_entities(client, file_info.get("caption", ""), None, None)

        for _ in range(20):
            try:
                r = await client.invoke(raw.functions.messages.SendMedia(
                    peer=peer, media=media, random_id=client.rnd_id(), **text,
                ))
            except FilePartMissing as e:
                # Telegram lost a part; re-upload only that chunk.
                is_big = isinstance(input_file, raw.types.InputFileBig)
                with open(filepath, "rb") as fp:
                    await self._send_part(self._media_sessions[0], fp, input_file.id,
                                          e.value, input_file.parts, is_big)
                continue

            for update in getattr(r, "updates", []):
                if isinstance(update, (raw.types.UpdateNewMessage, raw.types.UpdateNewChannelMessage)):
                    return update.message.id
            return None

        raise RuntimeError("Telegram kept reporting missing file parts")


def _read_part(fp, part: int) -> bytes:
    fp.seek(part * PART_SIZE)
    return fp.read(PART_SIZE)
