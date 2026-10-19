"""
Main Window
Primary application interface with folder selection, file preview,
rename controls, upload progress, and log display.
"""

import asyncio
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from loguru import logger

import config_manager
import database
import file_manager
import rename_manager
from telegram_service import TelegramService
from ui.settings_window import SettingsWindow


# Font that supports Khmer (Cambodian) script on Windows
KHMER_FONT_FAMILY = "Leelawadee UI"

MAX_LOG_LINES = 1000

STATUS_STYLE = {
    file_manager.STATUS_READY: ("Ready", "#8892b0"),
    file_manager.STATUS_UPLOADED: ("✔ Uploaded", "#4ecca3"),
    file_manager.STATUS_MISSING: ("⚠ Missing", "#e94560"),
    file_manager.STATUS_EMPTY: ("⚠ Empty", "#e94560"),
    file_manager.STATUS_TOO_LARGE: ("⚠ Too large", "#e94560"),
    file_manager.STATUS_PREMIUM_ONLY: ("⚠ > 2 GB", "#f0a500"),
}


def _fmt_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def _fmt_eta(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class MainWindow(ctk.CTk):
    """Root application window."""

    def __init__(self):
        super().__init__()

        # ---- Window setup -------------------------------------------------
        self.title("📤  Telegram Auto Uploader")
        self.geometry("960x800")
        self.minsize(820, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.configure(fg_color="#0a0a1a")

        # ---- State --------------------------------------------------------
        self._config: dict = config_manager.load_config()
        self._folder = ""
        self._video_files: list[str] = []
        self._file_sizes: dict[str, int] = {}
        self._previews: list[dict] = []
        self._rows: list[dict] = []
        self._statuses: list[str] = []
        self._is_uploading = False
        self._tg_service: TelegramService | None = None
        self._rename_after_id = None

        # Upload progress (only touched on the Tk thread)
        self._bytes_done: dict[int, int] = {}
        self._upload_total_bytes = 0
        self._files_done = 0
        self._files_total = 0
        self._speed = 0.0
        self._last_tick = (0.0, 0)

        # Background threads never touch widgets; they post callables here.
        self._ui_queue: queue.Queue = queue.Queue()

        # ---- Build UI -----------------------------------------------------
        self._build_menu()
        self._build_header()
        self._build_controls()
        # Bottom sections are packed first so the file list (which expands)
        # is the one that shrinks on small windows.
        self._build_footer()
        self._build_log_section()
        self._build_progress_section()
        self._build_file_list()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._process_ui_queue)

        # ---- Loguru sink to GUI -------------------------------------------
        self._log_sink_id = logger.add(
            self._log_sink, format="{time:HH:mm:ss} | {level.icon} | {message}", level="DEBUG",
        )
        logger.info(f"Application started. Settings are stored in {database.DATA_DIR}")
        if config_manager.validate_config(self._config):
            logger.info("Go to Settings to configure credentials.")

        self._restore_last_session()

    # ==================================================================
    #  MENU
    # ==================================================================

    def _build_menu(self):
        menubar = tk.Menu(self, bg="#16213e", fg="#ccd6f6",
                          activebackground="#e94560", activeforeground="#fff")
        file_menu = tk.Menu(menubar, tearoff=0, bg="#16213e", fg="#ccd6f6",
                            activebackground="#e94560", activeforeground="#fff")
        file_menu.add_command(label="⚙  Settings", command=self._open_settings)
        file_menu.add_command(label="📂  Open Data Folder", command=lambda: os.startfile(database.DATA_DIR))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="Menu", menu=file_menu)
        self.config(menu=menubar)

    # ==================================================================
    #  HEADER
    # ==================================================================

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent", height=70)
        hdr.pack(fill="x", padx=24, pady=(18, 0))

        title = ctk.CTkLabel(
            hdr, text="📤  Telegram Auto Uploader",
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
            text_color="#e94560",
        )
        title.pack(side="left")

        settings_btn = ctk.CTkButton(
            hdr, text="⚙  Settings", width=120, height=36,
            corner_radius=10,
            fg_color="#16213e", hover_color="#0f3460",
            border_width=1, border_color="#e94560",
            font=ctk.CTkFont(size=13),
            command=self._open_settings,
        )
        settings_btn.pack(side="right")

    # ==================================================================
    #  CONTROLS (folder + rename)
    # ==================================================================

    def _build_controls(self):
        ctl = ctk.CTkFrame(self, fg_color="#16213e", corner_radius=14)
        ctl.pack(fill="x", padx=24, pady=14)

        # Row 1: folder selector
        row1 = ctk.CTkFrame(ctl, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(14, 6))

        self.folder_label = ctk.CTkLabel(
            row1, text="No folder selected",
            font=ctk.CTkFont(size=13), text_color="#8892b0",
            anchor="w",
        )
        self.folder_label.pack(side="left", fill="x", expand=True)

        self.select_btn = ctk.CTkButton(
            row1, text="📁  Select Folder", width=160, height=38,
            corner_radius=10,
            fg_color="#e94560", hover_color="#c73052",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._select_folder,
        )
        self.select_btn.pack(side="right")

        # Row 2: rename pattern
        row2 = ctk.CTkFrame(ctl, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(6, 14))

        ctk.CTkLabel(
            row2, text="Rename As:",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#ccd6f6",
        ).pack(side="left", padx=(0, 8))

        self.rename_entry = ctk.CTkEntry(
            row2, placeholder_text="e.g. Kang Movie",
            height=38, corner_radius=8,
            fg_color="#0f3460", border_color="#e94560",
            text_color="#ccd6f6",
            font=ctk.CTkFont(family=KHMER_FONT_FAMILY, size=14),
        )
        self.rename_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.rename_entry.bind("<KeyRelease>", self._on_rename_typed)

        self.preview_btn = ctk.CTkButton(
            row2, text="🔄  Refresh", width=110, height=38,
            corner_radius=10,
            fg_color="#0f3460", hover_color="#16213e",
            border_width=1, border_color="#e94560",
            font=ctk.CTkFont(size=13),
            command=self._reload_folder,
        )
        self.preview_btn.pack(side="right")

    # ==================================================================
    #  FILE LIST (scrollable table)
    # ==================================================================

    def _build_file_list(self):
        wrapper = ctk.CTkFrame(self, fg_color="#16213e", corner_radius=14)
        wrapper.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # Column headers
        hdr = ctk.CTkFrame(wrapper, fg_color="#0f3460", corner_radius=8, height=34)
        hdr.pack(fill="x", padx=10, pady=(10, 4))
        for col, w in [("#", 40), ("Original Name", 270), ("→  New Name", 270), ("Size", 80), ("Status", 110)]:
            ctk.CTkLabel(hdr, text=col, width=w,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#e94560").pack(side="left", padx=6)

        self.file_scroll = ctk.CTkScrollableFrame(
            wrapper, fg_color="transparent",
        )
        self.file_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.file_count_label = ctk.CTkLabel(
            wrapper, text="0 files", font=ctk.CTkFont(size=12),
            text_color="#8892b0",
        )
        self.file_count_label.pack(pady=(0, 8))

    # ==================================================================
    #  PROGRESS
    # ==================================================================

    def _build_progress_section(self):
        pf = ctk.CTkFrame(self, fg_color="#16213e", corner_radius=14)
        pf.pack(side="bottom", fill="x", padx=24, pady=(0, 8))

        top_row = ctk.CTkFrame(pf, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(12, 4))

        self.progress_label = ctk.CTkLabel(
            top_row, text="Ready",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#ccd6f6",
        )
        self.progress_label.pack(side="left")

        self.percent_label = ctk.CTkLabel(
            top_row, text="0 %",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#e94560",
        )
        self.percent_label.pack(side="right")

        # Overall progress by bytes
        self.progress_bar = ctk.CTkProgressBar(
            pf, height=14, corner_radius=7,
            fg_color="#0f3460", progress_color="#e94560",
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=16, pady=(0, 4))

        # Files finished out of total
        self.file_progress_bar = ctk.CTkProgressBar(
            pf, height=8, corner_radius=4,
            fg_color="#0f3460", progress_color="#4ecca3",
        )
        self.file_progress_bar.set(0)
        self.file_progress_bar.pack(fill="x", padx=16, pady=(0, 12))

        # Buttons row
        btn_row = ctk.CTkFrame(pf, fg_color="transparent")
        btn_row.pack(pady=(0, 14))

        self.start_btn = ctk.CTkButton(
            btn_row, text="🚀  Start Upload", width=180, height=44,
            corner_radius=12,
            fg_color="#e94560", hover_color="#c73052",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start_upload,
        )
        self.start_btn.pack(side="left", padx=8)

        self.cancel_btn = ctk.CTkButton(
            btn_row, text="⛔  Cancel", width=120, height=44,
            corner_radius=12,
            fg_color="#16213e", hover_color="#0f3460",
            border_width=1, border_color="#e94560",
            font=ctk.CTkFont(size=14),
            command=self._cancel_upload,
            state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=8)

    # ==================================================================
    #  LOG
    # ==================================================================

    def _build_log_section(self):
        log_frame = ctk.CTkFrame(self, fg_color="#16213e", corner_radius=14)
        log_frame.pack(side="bottom", fill="x", padx=24, pady=(0, 6))

        ctk.CTkLabel(
            log_frame, text="📋  Activity Log",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#ccd6f6",
        ).pack(padx=16, pady=(10, 4), anchor="w")

        self.log_box = ctk.CTkTextbox(
            log_frame, height=90, corner_radius=8,
            fg_color="#0a0a1a", text_color="#8892b0",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.log_box.pack(fill="x", padx=14, pady=(0, 12))

    # ==================================================================
    #  FOOTER
    # ==================================================================

    def _build_footer(self):
        ft = ctk.CTkLabel(
            self, text="Telegram Auto Uploader  •  Built with Pyrogram MTProto & CustomTkinter",
            font=ctk.CTkFont(size=11), text_color="#4a4a6a",
        )
        ft.pack(side="bottom", pady=(0, 10))

    # ==================================================================
    #  ACTIONS
    # ==================================================================

    def _open_settings(self):
        SettingsWindow(self, on_save_callback=self._on_settings_saved,
                       is_busy=lambda: self._is_uploading)

    def _on_settings_saved(self, new_config: dict):
        self._config = config_manager.load_config()
        logger.info("Settings updated in main window.")
        if self._previews and not self._is_uploading:
            self._run_checks()

    def _restore_last_session(self):
        last_rename = str(self._config.get("last_rename", ""))
        if last_rename:
            self.rename_entry.insert(0, last_rename)
        last_folder = str(self._config.get("last_folder", ""))
        if last_folder and os.path.isdir(last_folder):
            self._load_folder(last_folder)

    # ---- Folder selection ------------------------------------------------

    def _select_folder(self):
        folder = filedialog.askdirectory(
            title="Select Video Folder", initialdir=self._folder or None,
        )
        if not folder:
            return
        config_manager.set_value("last_folder", folder)
        self._load_folder(folder)

    def _reload_folder(self):
        if self._folder:
            self._load_folder(self._folder)

    def _load_folder(self, folder: str):
        self._folder = folder
        self.folder_label.configure(text=folder, text_color="#4ecca3")
        self._video_files = file_manager.get_sorted_videos(folder)
        self._file_sizes = {}
        for path in self._video_files:
            try:
                self._file_sizes[path] = os.path.getsize(path)
            except OSError:
                self._file_sizes[path] = 0
        self._build_rows()
        self._run_checks()

    # ---- Preview ---------------------------------------------------------

    def _base_name(self) -> str:
        return self.rename_entry.get().strip() or "Video"

    def _on_rename_typed(self, _event=None):
        # Debounce: update names once the user pauses typing.
        if self._rename_after_id:
            self.after_cancel(self._rename_after_id)
        self._rename_after_id = self.after(250, self._apply_rename)

    def _apply_rename(self):
        self._rename_after_id = None
        config_manager.set_value("last_rename", self.rename_entry.get().strip())
        self._previews = rename_manager.preview_renames(self._video_files, self._base_name())
        for item, row in zip(self._previews, self._rows):
            row["new_label"].configure(text=item["new_name"])

    def _build_rows(self):
        self._previews = rename_manager.preview_renames(self._video_files, self._base_name())

        for widget in self.file_scroll.winfo_children():
            widget.destroy()
        self._rows = []

        for item in self._previews:
            row = ctk.CTkFrame(self.file_scroll, fg_color="#0f3460", corner_radius=8, height=32)
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(row, text=str(item["index"]), width=40,
                         font=ctk.CTkFont(size=12), text_color="#e94560").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=item["original"], width=270,
                         font=ctk.CTkFont(family=KHMER_FONT_FAMILY, size=12),
                         text_color="#8892b0",
                         anchor="w").pack(side="left", padx=6)
            new_label = ctk.CTkLabel(row, text=item["new_name"], width=270,
                                     font=ctk.CTkFont(family=KHMER_FONT_FAMILY, size=12, weight="bold"),
                                     text_color="#4ecca3", anchor="w")
            new_label.pack(side="left", padx=6)
            ctk.CTkLabel(row, text=_fmt_size(self._file_sizes.get(item["path"], 0)), width=80,
                         font=ctk.CTkFont(size=12), text_color="#8892b0").pack(side="left", padx=6)
            status_label = ctk.CTkLabel(row, text="", width=110, anchor="w",
                                        font=ctk.CTkFont(size=12), text_color="#8892b0")
            status_label.pack(side="left", padx=6)

            self._rows.append({"new_label": new_label, "status_label": status_label})

    # ---- Pre-upload checks -----------------------------------------------

    def _run_checks(self):
        """Check every file and show its status. Returns the status list."""
        chat_id = str(self._config.get("chat_id", "")).strip()
        uploaded = set()
        if chat_id:
            try:
                uploaded = database.get_uploaded_signatures(config_manager.chat_key(chat_id))
            except Exception as e:
                logger.warning(f"Could not read upload history: {e}")
        using_bot = bool(str(self._config.get("telegram_bot_token", "")).strip())

        self._statuses = [file_manager.check_file(p["path"], uploaded, using_bot) for p in self._previews]
        for status, row in zip(self._statuses, self._rows):
            text, color = STATUS_STYLE[status]
            row["status_label"].configure(text=text, text_color=color)

        counts: dict[str, int] = {}
        for s in self._statuses:
            counts[s] = counts.get(s, 0) + 1
        total_size = sum(self._file_sizes.values())
        parts = [f"{len(self._previews)} file(s) • {_fmt_size(total_size)}"]
        for status, (label, _) in STATUS_STYLE.items():
            if counts.get(status) and status != file_manager.STATUS_READY:
                parts.append(f"{counts[status]} {label.lstrip('✔⚠ ').lower()}")
        self.file_count_label.configure(text="  •  ".join(parts))
        return self._statuses

    # ---- Upload ----------------------------------------------------------

    def _start_upload(self):
        # Validate
        self._config = config_manager.load_config()
        errors = config_manager.validate_config(self._config)
        if errors:
            logger.error("Invalid config: " + "; ".join(errors))
            self._open_settings()
            return

        if not self._previews:
            logger.warning("No files to upload. Select a folder first.")
            return

        # Re-check right before uploading: files may have changed on disk.
        self._previews = rename_manager.preview_renames(self._video_files, self._base_name())
        statuses = self._run_checks()
        skip_uploaded = bool(self._config.get("skip_uploaded", True))

        sendable = {file_manager.STATUS_READY, file_manager.STATUS_PREMIUM_ONLY}
        if not skip_uploaded:
            sendable.add(file_manager.STATUS_UPLOADED)

        template = self._config.get("caption_template", "")
        base_name = self._base_name()
        upload_files = []
        for row, (item, status) in enumerate(zip(self._previews, statuses)):
            if status not in sendable:
                continue
            upload_files.append({
                "row": row,
                "path": item["path"],
                "new_name": item["new_name"],
                "index": item["index"],
                "caption": rename_manager.generate_caption(template, base_name, item["index"]),
                "size": self._file_sizes.get(item["path"], 0),
            })

        skipped = len(self._previews) - len(upload_files)
        if skipped:
            logger.info(f"Pre-check: skipping {skipped} file(s) (already uploaded or not uploadable).")
        if not upload_files:
            logger.warning(
                "Nothing to upload. To re-send files that were already uploaded, "
                "turn off 'Skip files already uploaded' in Settings."
            )
            return

        self._set_uploading(True)
        self._bytes_done = {}
        self._upload_total_bytes = sum(f["size"] for f in upload_files) or 1
        self._files_done = 0
        self._files_total = len(upload_files)
        self._speed = 0.0
        self._last_tick = (time.monotonic(), 0)
        self.progress_bar.set(0)
        self.file_progress_bar.set(0)
        self.percent_label.configure(text="0 %")
        logger.info(f"Uploading {len(upload_files)} file(s), {_fmt_size(self._upload_total_bytes)} total.")
        self.after(500, self._tick_progress)

        thread = threading.Thread(
            target=self._upload_thread,
            args=(upload_files, dict(self._config)),
            daemon=True,
        )
        thread.start()

    def _upload_thread(self, upload_files: list[dict], config: dict):
        """Runs in a background thread; drives async upload via its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._upload_async(upload_files, config))
        except Exception as e:
            logger.error(f"Upload thread crashed: {e}")
        finally:
            loop.close()
            self._post(lambda: self._set_uploading(False))

    async def _upload_async(self, upload_files: list[dict], config: dict):
        api_id = int(config["api_id"])
        api_hash = str(config["api_hash"]).strip()
        bot_token = str(config.get("telegram_bot_token", "")).strip()
        chat_id = config_manager.normalize_chat_id(config["chat_id"])
        history_key = config_manager.chat_key(config["chat_id"])

        self._tg_service = TelegramService(
            api_id, api_hash, bot_token=bot_token,
            parallel_uploads=int(config.get("parallel_uploads", 2)),
        )
        try:
            await self._tg_service.start()
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            self._post(lambda: self.progress_label.configure(text="❌ Could not connect to Telegram"))
            return

        def on_progress(file_info, current, total_bytes):
            row = file_info["row"]
            pct = int(current * 100 / total_bytes) if total_bytes else 0
            self._post(lambda: self._update_file_progress(row, current, f"⬆ {pct}%", "#f0a500"))

        def on_file_start(file_info):
            logger.info(f"Uploading: {file_info['new_name']}")
            row = file_info["row"]
            self._post(lambda: self._update_file_progress(row, 0, "⬆ 0%", "#f0a500"))

        def on_file_done(file_info, result):
            row = file_info["row"]
            size = file_info["size"]
            if result.success:
                logger.success(f"✅ Done: {result.filename}")
                try:
                    database.record_upload(history_key, file_info["path"], result.filename, result.message_id)
                except Exception as e:
                    logger.warning(f"Could not save upload history: {e}")
                status = ("✅ Sent", "#4ecca3")
            else:
                logger.error(f"❌ Failed: {result.filename} — {result.error}")
                status = ("❌ Failed", "#e94560")

            def apply():
                self._files_done += 1
                self._update_file_progress(row, size, *status)
            self._post(apply)

        try:
            started = time.monotonic()
            results = await self._tg_service.upload_files(
                chat_id=chat_id,
                files=upload_files,
                send_as_video=config.get("send_as_video", True),
                delay=int(config.get("upload_delay", 2)),
                on_progress=on_progress,
                on_file_start=on_file_start,
                on_file_done=on_file_done,
            )

            elapsed = max(time.monotonic() - started, 0.001)
            success_count = sum(1 for r in results if r.success)
            fail_count = len(results) - success_count
            not_started = len(upload_files) - len(results)
            sent_bytes = sum(f["size"] for f, r in zip(upload_files, results) if r.success)
            msg = f"✅ Upload Complete — {success_count} succeeded, {fail_count} failed"
            if not_started:
                msg += f", {not_started} not started"
            msg += f"  ({_fmt_size(sent_bytes / elapsed)}/s avg, {_fmt_eta(elapsed)})"
            logger.info(msg)
            self._post(lambda: self.progress_label.configure(text=msg))

        except Exception as e:
            logger.error(f"Upload error: {e}")
            self._post(lambda: self.progress_label.configure(text=f"❌ {e}"[:120]))
        finally:
            await self._tg_service.stop()

    def _update_file_progress(self, row: int, done_bytes: int, status: str, color: str):
        self._bytes_done[row] = done_bytes
        if row < len(self._rows):
            self._rows[row]["status_label"].configure(text=status, text_color=color)

    def _tick_progress(self):
        """Refresh overall progress, speed and ETA twice a second while uploading."""
        if not self._is_uploading:
            return
        now = time.monotonic()
        done = sum(self._bytes_done.values())
        last_time, last_done = self._last_tick
        dt = now - last_time
        if dt > 0:
            instant = max(done - last_done, 0) / dt
            self._speed = instant if self._speed == 0 else 0.3 * instant + 0.7 * self._speed
        self._last_tick = (now, done)

        frac = min(done / self._upload_total_bytes, 1.0)
        self.progress_bar.set(frac)
        self.file_progress_bar.set(self._files_done / self._files_total if self._files_total else 0)
        self.percent_label.configure(text=f"{int(frac * 100)} %")

        text = f"Uploading  •  {self._files_done}/{self._files_total} done"
        if self._speed > 0:
            remaining = max(self._upload_total_bytes - done, 0)
            text += f"  •  {_fmt_size(self._speed)}/s  •  ETA {_fmt_eta(remaining / self._speed)}"
        self.progress_label.configure(text=text)
        self.after(500, self._tick_progress)

    def _cancel_upload(self):
        if self._tg_service:
            self._tg_service.request_cancel()
            logger.warning("Cancel requested — stopping uploads…")
            self.cancel_btn.configure(state="disabled")

    def _on_close(self):
        if self._is_uploading:
            if not messagebox.askyesno("Upload in progress", "An upload is running. Stop it and exit?"):
                return
            self._cancel_upload()
        try:
            logger.remove(self._log_sink_id)
        except ValueError:
            pass
        self.destroy()

    # ---- UI state --------------------------------------------------------

    def _set_uploading(self, uploading: bool):
        self._is_uploading = uploading
        state = "disabled" if uploading else "normal"
        cancel_state = "normal" if uploading else "disabled"

        self.start_btn.configure(state=state)
        self.select_btn.configure(state=state)
        self.rename_entry.configure(state=state)
        self.preview_btn.configure(state=state)
        self.cancel_btn.configure(state=cancel_state)

        if not uploading:
            frac = self._files_done / self._files_total if self._files_total else 0
            self.file_progress_bar.set(frac)

    # ---- Thread-safe UI updates -------------------------------------------

    def _post(self, fn):
        """Run *fn* on the Tk thread. Safe to call from any thread."""
        self._ui_queue.put(fn)

    def _process_ui_queue(self):
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass  # widget may be destroyed
        except queue.Empty:
            pass
        self.after(50, self._process_ui_queue)

    # ---- Loguru sink -----------------------------------------------------

    def _log_sink(self, message):
        """Custom loguru sink that appends messages to the GUI log box."""
        text = str(message).strip() + "\n"
        self._post(lambda: self._append_log(text))

    def _append_log(self, text: str):
        self.log_box.insert("end", text)
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_box.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_box.see("end")
