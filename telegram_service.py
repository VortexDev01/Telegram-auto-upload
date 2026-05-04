"""
Telegram upload service.

Handles connecting to Telegram via Pyrogram and uploading video files
sequentially with flood-wait handling.
"""

import asyncio
import json
import os
from typing import Callable, Optional

from loguru import logger
from pyrogram import Client
from pyrogram.errors import FloodWait

# State file for resume support
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "upload_state.json")


class UploadResult:
    """Stores the outcome of a single file upload."""

    def __init__(self, filename: str, success: bool, error: str = ""):
        self.filename = filename
        self.success = success
        self.error = error


class ChatResolutionError(Exception):
    """Raised when the configured target chat cannot be resolved."""


class TelegramService:
    """
    Manages the Pyrogram client lifecycle and sequential uploads.

    Usage:
        svc = TelegramService(api_id, api_hash, bot_token=...)
        await svc.start()
        results = await svc.upload_files(chat_id, file_list, ...)
        await svc.stop()
    """

    def __init__(self, api_id: int, api_hash: str, bot_token: str = ""):
        self._api_id = api_id
        self._api_hash = api_hash
        self._bot_token = bot_token.strip()
        self._client: Optional[Client] = None
        self._cancel_requested = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start and authenticate the Pyrogram client."""
        session_dir = os.path.dirname(os.path.abspath(__file__))
        client_kwargs = {
            "name": "autoupload_bot_session" if self._bot_token else "autoupload_session",
            "api_id": self._api_id,
            "api_hash": self._api_hash,
            "workdir": session_dir,
        }
        if self._bot_token:
            client_kwargs["bot_token"] = self._bot_token

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
        """Signal the upload loop to stop after the current file."""
        self._cancel_requested = True

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_files(
        self,
        chat_id: str | int,
        files: list[dict],
        caption_template: str = "",
        send_as_video: bool = True,
        delay: int = 2,
        max_retries: int = 3,
        on_progress: Optional[Callable] = None,
        on_file_start: Optional[Callable] = None,
        on_file_done: Optional[Callable] = None,
    ) -> list[UploadResult]:
        """
        Upload *files* sequentially to *chat_id*.

        Parameters
        ----------
        files : list[dict]
            Each dict must have keys: path, new_name, index
        caption_template : str
            Caption with {name} and {index} placeholders.
        on_progress : callable(current, total, filename)
            Called during each file's upload.
        on_file_start : callable(index, total, filename)
            Called when a new file starts uploading.
        on_file_done : callable(index, total, filename, success, error)
            Called when a file finishes.
        """
        self._cancel_requested = False
        results: list[UploadResult] = []
        total = len(files)
        chat_id = await self._resolve_chat_id(chat_id)

        # Determine resume index
        start_index = self._load_resume_state(files)

        for i, file_info in enumerate(files):
            if i < start_index:
                # Already uploaded in a previous run
                results.append(UploadResult(file_info["new_name"], True))
                continue

            if self._cancel_requested:
                logger.warning("Upload cancelled by user.")
                break

            filename = file_info["new_name"]
            filepath = file_info["path"]
            index = file_info["index"]

            if on_file_start:
                on_file_start(i + 1, total, filename)

            try:
                caption = caption_template.format(
                    name=filename.rsplit(".", 1)[0],
                    index=index,
                )
            except (KeyError, IndexError):
                caption = filename

            result = await self._upload_single(
                chat_id=chat_id,
                filepath=filepath,
                filename=filename,
                caption=caption,
                send_as_video=send_as_video,
                max_retries=max_retries,
                on_progress=on_progress,
            )
            results.append(result)

            if on_file_done:
                on_file_done(i + 1, total, filename, result.success, result.error)

            self._save_resume_state(i + 1, files)

            if i < total - 1 and not self._cancel_requested:
                await asyncio.sleep(delay)

        if not self._cancel_requested:
            self._clear_resume_state()

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_chat_id(self, chat_id: str | int) -> str | int:
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

    async def _upload_single(
        self,
        chat_id: str | int,
        filepath: str,
        filename: str,
        caption: str,
        send_as_video: bool,
        max_retries: int,
        on_progress: Optional[Callable],
    ) -> UploadResult:
        """Upload a single file with retry and FloodWait handling."""

        if not os.path.exists(filepath):
            err = f"File not found: {filepath}"
            logger.error(err)
            return UploadResult(filename, False, err)

        def _progress_wrapper(current: int, total: int):
            if on_progress:
                on_progress(current, total, filename)

        for attempt in range(1, max_retries + 1):
            try:
                if send_as_video:
                    await self._client.send_video(
                        chat_id=chat_id,
                        video=filepath,
                        caption=caption,
                        file_name=filename,
                        supports_streaming=True,
                        progress=_progress_wrapper,
                    )
                else:
                    await self._client.send_document(
                        chat_id=chat_id,
                        document=filepath,
                        caption=caption,
                        file_name=filename,
                        progress=_progress_wrapper,
                    )

                logger.success(f"Uploaded: {filename}")
                return UploadResult(filename, True)

            except FloodWait as e:
                wait = e.value
                logger.warning(f"FloodWait: sleeping {wait}s before retrying {filename}")
                await asyncio.sleep(wait)
                continue

            except Exception as e:
                logger.error(f"Attempt {attempt}/{max_retries} failed for {filename}: {e}")
                if attempt == max_retries:
                    return UploadResult(filename, False, str(e))
                await asyncio.sleep(3)

        return UploadResult(filename, False, "Max retries exceeded.")

    # ------------------------------------------------------------------
    # Resume support
    # ------------------------------------------------------------------

    @staticmethod
    def _save_resume_state(completed_index: int, files: list[dict]) -> None:
        try:
            state = {
                "completed_index": completed_index,
                "files_hash": hash(tuple(f["path"] for f in files)),
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except IOError:
            pass

    @staticmethod
    def _load_resume_state(files: list[dict]) -> int:
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                current_hash = hash(tuple(fi["path"] for fi in files))
                if state.get("files_hash") == current_hash:
                    idx = state.get("completed_index", 0)
                    if idx > 0:
                        logger.info(f"Resuming from file #{idx + 1}")
                    return idx
        except (IOError, json.JSONDecodeError):
            pass
        return 0

    @staticmethod
    def _clear_resume_state() -> None:
        try:
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
        except IOError:
            pass
