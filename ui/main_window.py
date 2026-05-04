"""
Main Window
Primary application interface with folder selection, file preview,
rename controls, upload progress, and log display.
"""

import asyncio
import os
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
from loguru import logger

import config_manager
import file_manager
import rename_manager
from telegram_service import TelegramService
from ui.settings_window import SettingsWindow


# Font that supports Khmer (Cambodian) script on Windows
KHMER_FONT_FAMILY = "Leelawadee UI"


class MainWindow(ctk.CTk):
    """Root application window."""

    def __init__(self):
        super().__init__()

        # ---- Window setup -------------------------------------------------
        self.title("📤  Telegram Auto Uploader")
        self.geometry("960x720")
        self.minsize(820, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.configure(fg_color="#0a0a1a")

        # ---- State --------------------------------------------------------
        self._config: dict = config_manager.load_config()
        self._video_files: list[str] = []
        self._previews: list[dict] = []
        self._is_uploading = False
        self._tg_service: TelegramService | None = None

        # ---- Build UI -----------------------------------------------------
        self._build_menu()
        self._build_header()
        self._build_controls()
        self._build_file_list()
        self._build_progress_section()
        self._build_log_section()
        self._build_footer()

        # ---- Loguru sink to GUI -------------------------------------------
        logger.add(self._log_sink, format="{time:HH:mm:ss} | {level.icon} | {message}", level="DEBUG")
        logger.info("Application started. Go to Settings to configure credentials.")

    # ==================================================================
    #  MENU
    # ==================================================================

    def _build_menu(self):
        menubar = tk.Menu(self, bg="#16213e", fg="#ccd6f6",
                          activebackground="#e94560", activeforeground="#fff")
        file_menu = tk.Menu(menubar, tearoff=0, bg="#16213e", fg="#ccd6f6",
                            activebackground="#e94560", activeforeground="#fff")
        file_menu.add_command(label="⚙  Settings", command=self._open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
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
        self.rename_entry.bind("<KeyRelease>", lambda e: self._refresh_preview())

        self.preview_btn = ctk.CTkButton(
            row2, text="🔄  Preview", width=110, height=38,
            corner_radius=10,
            fg_color="#0f3460", hover_color="#16213e",
            border_width=1, border_color="#e94560",
            font=ctk.CTkFont(size=13),
            command=self._refresh_preview,
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
        for col, w in [("#", 40), ("Original Name", 300), ("→  New Name", 300), ("Size", 90)]:
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
        pf.pack(fill="x", padx=24, pady=(0, 8))

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

        self.progress_bar = ctk.CTkProgressBar(
            pf, height=14, corner_radius=7,
            fg_color="#0f3460", progress_color="#e94560",
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=16, pady=(0, 4))

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
        log_frame.pack(fill="x", padx=24, pady=(0, 6))

        ctk.CTkLabel(
            log_frame, text="📋  Activity Log",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#ccd6f6",
        ).pack(padx=16, pady=(10, 4), anchor="w")

        self.log_box = ctk.CTkTextbox(
            log_frame, height=110, corner_radius=8,
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
        ft.pack(pady=(0, 10))

    # ==================================================================
    #  ACTIONS
    # ==================================================================

    def _open_settings(self):
        SettingsWindow(self, on_save_callback=self._on_settings_saved)

    def _on_settings_saved(self, new_config: dict):
        self._config = new_config
        logger.info("Settings updated in main window.")

    # ---- Folder selection ------------------------------------------------

    def _select_folder(self):
        folder = filedialog.askdirectory(title="Select Video Folder")
        if not folder:
            return
        self.folder_label.configure(text=folder, text_color="#4ecca3")
        self._video_files = file_manager.get_sorted_videos(folder)
        self._refresh_preview()

    # ---- Preview ---------------------------------------------------------

    def _refresh_preview(self, *_):
        base_name = self.rename_entry.get().strip()
        if not base_name:
            base_name = "Video"

        self._previews = rename_manager.preview_renames(self._video_files, base_name)

        # Clear old rows
        for widget in self.file_scroll.winfo_children():
            widget.destroy()

        for item in self._previews:
            info = file_manager.get_file_info(item["path"])
            row = ctk.CTkFrame(self.file_scroll, fg_color="#0f3460", corner_radius=8, height=32)
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(row, text=str(item["index"]), width=40,
                         font=ctk.CTkFont(size=12), text_color="#e94560").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=item["original"], width=300,
                         font=ctk.CTkFont(family=KHMER_FONT_FAMILY, size=12),
                         text_color="#8892b0",
                         anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=item["new_name"], width=300,
                         font=ctk.CTkFont(family=KHMER_FONT_FAMILY, size=12, weight="bold"),
                         text_color="#4ecca3", anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=f"{info['size_mb']} MB", width=90,
                         font=ctk.CTkFont(size=12), text_color="#8892b0").pack(side="left", padx=6)

        self.file_count_label.configure(text=f"{len(self._previews)} file(s)")

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

        self._set_uploading(True)

        # Prepare file dicts for the service
        upload_files = [
            {"path": p["path"], "new_name": p["new_name"], "index": p["index"]}
            for p in self._previews
        ]

        # Run upload in background thread
        thread = threading.Thread(
            target=self._upload_thread,
            args=(upload_files,),
            daemon=True,
        )
        thread.start()

    def _upload_thread(self, upload_files: list[dict]):
        """Runs in a background thread; drives async upload via its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._upload_async(upload_files))
        except Exception as e:
            logger.error(f"Upload thread crashed: {e}")
        finally:
            loop.close()
            self.after(0, lambda: self._set_uploading(False))

    async def _upload_async(self, upload_files: list[dict]):
        api_id = int(self._config["api_id"])
        api_hash = self._config["api_hash"]
        bot_token = str(self._config.get("telegram_bot_token", "")).strip()
        chat_id = self._config["chat_id"]

        # Try to parse chat_id as int if it looks numeric
        try:
            chat_id = int(chat_id)
        except ValueError:
            pass  # keep as string (e.g. "@channel_name")

        self._tg_service = TelegramService(api_id, api_hash, bot_token=bot_token)
        try:
            await self._tg_service.start()
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            return

        total = len(upload_files)

        def on_progress(current, total_bytes, filename):
            pct = current / total_bytes if total_bytes else 0
            self.after(0, lambda: self.file_progress_bar.set(pct))

        def on_file_start(idx, tot, name):
            logger.info(f"[{idx}/{tot}] Uploading: {name}")
            overall = (idx - 1) / tot
            self.after(0, lambda: self.progress_bar.set(overall))
            self.after(0, lambda: self.progress_label.configure(text=f"Uploading {idx}/{tot}: {name}"))
            self.after(0, lambda: self.percent_label.configure(text=f"{int(overall * 100)} %"))
            self.after(0, lambda: self.file_progress_bar.set(0))

        def on_file_done(idx, tot, name, success, error):
            if success:
                logger.success(f"[{idx}/{tot}] ✅ Done: {name}")
            else:
                logger.error(f"[{idx}/{tot}] ❌ Failed: {name} — {error}")
            overall = idx / tot
            self.after(0, lambda: self.progress_bar.set(overall))
            self.after(0, lambda: self.percent_label.configure(text=f"{int(overall * 100)} %"))

        try:
            results = await self._tg_service.upload_files(
                chat_id=chat_id,
                files=upload_files,
                caption_template=self._config.get("caption_template", ""),
                send_as_video=self._config.get("send_as_video", True),
                delay=int(self._config.get("upload_delay", 2)),
                on_progress=on_progress,
                on_file_start=on_file_start,
                on_file_done=on_file_done,
            )

            success_count = sum(1 for r in results if r.success)
            fail_count = len(results) - success_count
            msg = f"✅ Upload Complete — {success_count} succeeded, {fail_count} failed"
            logger.info(msg)
            self.after(0, lambda: self.progress_label.configure(text=msg))

        except Exception as e:
            logger.error(f"Upload error: {e}")
        finally:
            await self._tg_service.stop()

    def _cancel_upload(self):
        if self._tg_service:
            self._tg_service.request_cancel()
            logger.warning("Cancel requested — will stop after current file.")

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
            self.file_progress_bar.set(0)

    # ---- Loguru sink -----------------------------------------------------

    def _log_sink(self, message):
        """Custom loguru sink that appends messages to the GUI log box."""
        text = str(message).strip() + "\n"
        # Schedule on main thread
        self.after(0, lambda: self._append_log(text))

    def _append_log(self, text: str):
        try:
            self.log_box.insert("end", text)
            self.log_box.see("end")
        except Exception:
            pass  # widget may be destroyed
