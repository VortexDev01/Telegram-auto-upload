"""
Telegram upload service.

Handles connecting to Telegram via Pyrogram and uploading video files.
Several files upload in parallel, but messages are always posted in the
original order so episodes stay sorted in the chat.
"""

import asyncio
import os
import sys
import time
from typing import Callable, Optional

from loguru import logger
from pyrogram import Client, raw, utils
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


class UploadResult:
    """Stores the outcome of a single file upload."""

    def __init__(self, filename: str, success: bool, error: str = "",
                 message_id: int | None = None, fatal: bool = False):
        self.filename = filename
        self.success = success
        self.error = error
        self.message_id = message_id
        self.fatal = fatal  # the same error would hit every remaining file


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
            "max_concurrent_transmissions": self._parallel,
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
            if not os.path.isfile(filepath):
                raise UploadError(f"File not found: {filepath}")

            if on_file_start:
                on_file_start(file_info)

            last_report = 0.0

            # An async callback avoids Pyrogram's thread-pool hop for every chunk.
            async def progress(current: int, total: int):
                nonlocal last_report
                now = time.monotonic()
                if current < total and now - last_report < PROGRESS_INTERVAL:
                    return
                last_report = now
                if on_progress:
                    on_progress(file_info, current, total)

            attempt = 0
            while True:
                attempt += 1
                try:
                    input_file = await self._client.save_file(filepath, progress=progress)
                    if input_file is None:
                        raise RuntimeError("upload was interrupted (connection problem)")
                    return input_file
                except FloodWait as e:
                    logger.warning(f"FloodWait: sleeping {e.value}s before retrying {filename}")
                    await asyncio.sleep(e.value)
                    attempt -= 1
                except (ValueError, OSError) as e:
                    # Empty file, too large, unreadable — retrying won't help.
                    raise UploadError(str(e)) from e
                except Exception as e:
                    logger.error(f"Upload attempt {attempt}/{max_retries} failed for {filename}: {e}")
                    if attempt >= max_retries:
                        raise UploadError(str(e)) from e
                    await asyncio.sleep(3 * attempt)

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
                await client.save_file(filepath, file_id=input_file.id, file_part=e.value)
                continue

            for update in getattr(r, "updates", []):
                if isinstance(update, (raw.types.UpdateNewMessage, raw.types.UpdateNewChannelMessage)):
                    return update.message.id
            return None

        raise RuntimeError("Telegram kept reporting missing file parts")
