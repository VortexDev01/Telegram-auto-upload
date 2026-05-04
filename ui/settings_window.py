"""
Settings Window
Modal dialog for entering API ID, API Hash, Target Chat ID, Telegram Bot Token, and preferences.
"""

import customtkinter as ctk
from loguru import logger

import config_manager


class SettingsWindow(ctk.CTkToplevel):
    """A modal settings dialog."""

    def __init__(self, parent, on_save_callback=None):
        super().__init__(parent)
        self.title("⚙  Settings")
        self.geometry("540x730")
        self.resizable(False, False)
        self.grab_set()  # modal
        self.transient(parent)
        self._on_save = on_save_callback
        self._config = config_manager.load_config()
        self._show_token = False  # toggle for showing/hiding bot token

        self.configure(fg_color="#1a1a2e")
        self._build_ui()
        self._populate()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = ctk.CTkLabel(
            self, text="⚙  Application Settings",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e94560",
        )
        header.pack(pady=(24, 4))

        subtitle = ctk.CTkLabel(
            self, text="Configure your Telegram API credentials",
            font=ctk.CTkFont(size=12), text_color="#8892b0",
        )
        subtitle.pack(pady=(0, 20))

        # ---- Scrollable form area ----
        form = ctk.CTkFrame(self, fg_color="#16213e", corner_radius=14)
        form.pack(padx=28, pady=0, fill="x")

        # --- API ID ---
        self._make_label(form, "API ID")
        self.api_id_entry = ctk.CTkEntry(
            form, placeholder_text="e.g. 12345678",
            height=38, corner_radius=8,
            fg_color="#0f3460", border_color="#e94560", text_color="#ccd6f6",
        )
        self.api_id_entry.pack(padx=20, fill="x")

        # --- API Hash ---
        self._make_label(form, "API Hash")
        self.api_hash_entry = ctk.CTkEntry(
            form, placeholder_text="e.g. a1b2c3d4e5f6...",
            height=38, corner_radius=8,
            fg_color="#0f3460", border_color="#e94560", text_color="#ccd6f6",
        )
        self.api_hash_entry.pack(padx=20, fill="x")

        # --- Chat ID ---
        self._make_label(form, "Target Chat ID")
        self.chat_id_entry = ctk.CTkEntry(
            form, placeholder_text="e.g. -1001234567890 or @channel",
            height=38, corner_radius=8,
            fg_color="#0f3460", border_color="#e94560", text_color="#ccd6f6",
        )
        self.chat_id_entry.pack(padx=20, fill="x")

        # --- Upload Delay ---
        self._make_label(form, "Delay Between Uploads (seconds)")
        self.delay_entry = ctk.CTkEntry(
            form, placeholder_text="2",
            height=38, corner_radius=8,
            fg_color="#0f3460", border_color="#e94560", text_color="#ccd6f6",
        )
        self.delay_entry.pack(padx=20, fill="x")

        # --- Caption Template ---
        self._make_label(form, "Caption Template")
        self.caption_entry = ctk.CTkEntry(
            form, placeholder_text="{name}  🎬 Episode {index}",
            height=38, corner_radius=8,
            fg_color="#0f3460", border_color="#e94560", text_color="#ccd6f6",
        )
        self.caption_entry.pack(padx=20, fill="x")

        # --- Send As Video toggle ---
        self.send_as_video_var = ctk.BooleanVar(value=True)
        self.video_switch = ctk.CTkSwitch(
            form, text="Send as Video (vs Document)",
            variable=self.send_as_video_var,
            onvalue=True, offvalue=False,
            font=ctk.CTkFont(size=13),
            text_color="#ccd6f6",
            progress_color="#e94560",
        )
        self.video_switch.pack(padx=20, pady=(14, 16), anchor="w")

        # ==============================================================
        #  TELEGRAM BOT API KEY SECTION
        # ==============================================================
        bot_frame = ctk.CTkFrame(self, fg_color="#16213e", corner_radius=14)
        bot_frame.pack(padx=28, pady=(12, 0), fill="x")

        bot_header_row = ctk.CTkFrame(bot_frame, fg_color="transparent")
        bot_header_row.pack(fill="x", padx=20, pady=(14, 4))

        ctk.CTkLabel(
            bot_header_row, text="🤖  Telegram Bot API Key",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#4ecca3",
        ).pack(side="left")

        # Show / Hide toggle button
        self.toggle_token_btn = ctk.CTkButton(
            bot_header_row, text="👁  Show", width=70, height=28,
            corner_radius=6,
            fg_color="#0f3460", hover_color="#16213e",
            border_width=1, border_color="#4ecca3",
            font=ctk.CTkFont(size=11),
            command=self._toggle_token_visibility,
        )
        self.toggle_token_btn.pack(side="right")

        self.bot_token_entry = ctk.CTkEntry(
            bot_frame, placeholder_text="e.g. 123456:ABC-DEF1234ghIkl-zyx57W2v...",
            height=38, corner_radius=8,
            fg_color="#0f3460", border_color="#4ecca3", text_color="#ccd6f6",
            show="•",
        )
        self.bot_token_entry.pack(padx=20, pady=(4, 4), fill="x")

        # Store API Key button
        self.store_btn = ctk.CTkButton(
            bot_frame, text="🔑  Store API Key", width=160, height=38,
            corner_radius=10,
            fg_color="#4ecca3", hover_color="#3bae8a",
            text_color="#0a0a1a",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._store_bot_token,
        )
        self.store_btn.pack(pady=(4, 6))

        # Status label for bot key
        self.bot_status_label = ctk.CTkLabel(
            bot_frame, text="", font=ctk.CTkFont(size=11),
            text_color="#8892b0",
        )
        self.bot_status_label.pack(pady=(0, 12))

        # --- Main Buttons ---
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=16)

        save_btn = ctk.CTkButton(
            btn_frame, text="💾  Save All", width=140, height=42,
            corner_radius=10,
            fg_color="#e94560", hover_color="#c73052",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_save_click,
        )
        save_btn.pack(side="left", padx=8)

        cancel_btn = ctk.CTkButton(
            btn_frame, text="Cancel", width=120, height=42,
            corner_radius=10,
            fg_color="#16213e", hover_color="#0f3460",
            border_width=1, border_color="#e94560",
            font=ctk.CTkFont(size=14),
            command=self.destroy,
        )
        cancel_btn.pack(side="left", padx=8)

        # Global status label
        self.status_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12),
            text_color="#e94560",
        )
        self.status_label.pack(pady=(0, 10))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_label(parent, text: str):
        lbl = ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#ccd6f6",
        )
        lbl.pack(padx=20, pady=(14, 4), anchor="w")

    def _populate(self):
        """Fill entries from loaded config."""
        c = self._config
        self.api_id_entry.insert(0, str(c.get("api_id", "")))
        self.api_hash_entry.insert(0, str(c.get("api_hash", "")))
        self.chat_id_entry.insert(0, str(c.get("chat_id", "")))
        self.delay_entry.insert(0, str(c.get("upload_delay", 2)))
        self.caption_entry.insert(0, str(c.get("caption_template", "")))
        self.send_as_video_var.set(c.get("send_as_video", True))
        # Bot token
        bot_token = str(c.get("telegram_bot_token", ""))
        if bot_token:
            self.bot_token_entry.insert(0, bot_token)
            self.bot_status_label.configure(
                text="✅ API Key is configured",
                text_color="#4ecca3",
            )

    def _toggle_token_visibility(self):
        """Toggle showing/hiding the bot token."""
        self._show_token = not self._show_token
        if self._show_token:
            self.bot_token_entry.configure(show="")
            self.toggle_token_btn.configure(text="🙈  Hide")
        else:
            self.bot_token_entry.configure(show="•")
            self.toggle_token_btn.configure(text="👁  Show")

    def _store_bot_token(self):
        """Store only the Telegram Bot API key immediately."""
        token = self.bot_token_entry.get().strip()
        api_id = self.api_id_entry.get().strip()
        api_hash = self.api_hash_entry.get().strip()
        chat_id = self.chat_id_entry.get().strip()
        if not token or not api_id or not api_hash or not chat_id:
            self.bot_status_label.configure(
                text="⚠ Please enter a bot token first.",
                text_color="#e94560",
            )
            return

        # Basic format validation: Telegram bot tokens look like 123456:ABC-DEF...
        if ":" not in token or len(token) < 20:
            self.bot_status_label.configure(
                text="⚠ Token format looks invalid (expected 123456:ABC-DEF...)",
                text_color="#e94560",
            )
            return

        # Save token to config
        current_config = config_manager.load_config()
        current_config["telegram_bot_token"] = token
        current_config["api_id"] = api_id
        current_config["api_hash"] = api_hash
        current_config["chat_id"] = chat_id
        if config_manager.save_config(current_config):
            self._config = current_config
            self.bot_status_label.configure(
                text="✅ Bot API Key stored successfully!",
                text_color="#4ecca3",
            )
            logger.info("Telegram Bot API Key stored.")
        else:
            self.bot_status_label.configure(
                text="❌ Failed to save. Check file permissions.",
                text_color="#e94560",
            )

    def _on_save_click(self):
        new_config = {
            "api_id": self.api_id_entry.get().strip(),
            "api_hash": self.api_hash_entry.get().strip(),
            "chat_id": self.chat_id_entry.get().strip(),
            "telegram_bot_token": self.bot_token_entry.get().strip(),
            "upload_delay": int(self.delay_entry.get().strip() or 2),
            "caption_template": self.caption_entry.get().strip(),
            "send_as_video": self.send_as_video_var.get(),
        }

        errors = config_manager.validate_config(new_config)
        if errors:
            self.status_label.configure(text="\n".join(errors))
            return

        config_manager.save_config(new_config)
        self.status_label.configure(text="✅ All settings saved!", text_color="#4ecca3")
        logger.info("Settings saved via dialog.")

        if self._on_save:
            self._on_save(new_config)

        self.after(800, self.destroy)
