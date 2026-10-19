import re

from loguru import logger

import database

DEFAULT_CONFIG = {
    "api_id": "",
    "api_hash": "",
    "chat_id": "",
    "telegram_bot_token": "",
    "upload_delay": 2,
    "caption_template": "{name}\n🎬 Episode {index}",
    "send_as_video": True,
    "parallel_uploads": 2,
    "skip_uploaded": True,
    # Remembered UI state
    "last_folder": "",
    "last_rename": "",
}

MAX_PARALLEL_UPLOADS = 4
MAX_CAPTION_LENGTH = 1024

_BOT_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
_API_HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_TME_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(.+?)/?$", re.IGNORECASE)


def load_config() -> dict:
    """Load configuration from the local database, filled in with defaults."""
    try:
        stored = database.get_all_settings()
        return {**DEFAULT_CONFIG, **stored}
    except Exception as e:
        logger.warning(f"Failed to read settings database, using defaults: {e}")
        return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> bool:
    """Save configuration values to the local database. Returns True on success."""
    try:
        database.set_settings(config)
        logger.info("Configuration saved.")
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def set_value(key: str, value) -> None:
    """Persist a single value quietly (used for remembered UI state)."""
    try:
        database.set_settings({key: value})
    except Exception as e:
        logger.debug(f"Could not remember {key}: {e}")


def normalize_chat_id(value) -> str | int:
    """
    Turn user input into something Pyrogram accepts:
    "-100123" -> -100123, "https://t.me/name" -> "name", "@name" -> "name".
    """
    target = str(value).strip()
    m = _TME_RE.match(target)
    if m:
        target = m.group(1)
    if target.lstrip("-").isdigit():
        return int(target)
    return target.lstrip("@")


def chat_key(value) -> str:
    """Stable key used to group upload history per target chat."""
    return str(normalize_chat_id(value)).lower()


def validate_config(config: dict) -> list[str]:
    """
    Validate config fields.
    Returns a list of human-readable error strings (empty = valid).
    """
    errors: list[str] = []

    api_id = str(config.get("api_id", "")).strip()
    if not api_id.isdigit():
        errors.append("API ID must be a number (from my.telegram.org).")

    api_hash = str(config.get("api_hash", "")).strip()
    if not _API_HASH_RE.match(api_hash):
        errors.append("API Hash must be the 32-character hex string from my.telegram.org.")

    raw_chat = str(config.get("chat_id", "")).strip()
    if not raw_chat:
        errors.append("Target Chat ID is required.")
    else:
        if "/+" in raw_chat or "joinchat" in raw_chat.lower():
            errors.append("Invite links can't be used as Chat ID. Use the numeric ID (-100...) or @username.")
        else:
            target = normalize_chat_id(raw_chat)
            if isinstance(target, str) and target.lower() not in ("me", "self") and not _USERNAME_RE.match(target):
                errors.append("Chat ID must be a numeric ID (e.g. -1001234567890) or a @username.")

    token = str(config.get("telegram_bot_token", "")).strip()
    if token and not _BOT_TOKEN_RE.match(token):
        errors.append("Bot token format looks invalid (expected 123456789:ABC-DEF...).")

    try:
        delay = int(config.get("upload_delay", 0))
        if not 0 <= delay <= 300:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Delay must be a whole number between 0 and 300 seconds.")

    try:
        parallel = int(config.get("parallel_uploads", 1))
        if not 1 <= parallel <= MAX_PARALLEL_UPLOADS:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"Parallel uploads must be between 1 and {MAX_PARALLEL_UPLOADS}.")

    template = str(config.get("caption_template", ""))
    try:
        sample = template.format(name="Sample Name 100", index=100)
        if len(sample) > MAX_CAPTION_LENGTH:
            errors.append(f"Caption is too long ({len(sample)} chars, Telegram allows {MAX_CAPTION_LENGTH}).")
    except (KeyError, IndexError, ValueError) as e:
        errors.append(f"Caption template is invalid ({e}). Only {{name}} and {{index}} are supported.")

    return errors
