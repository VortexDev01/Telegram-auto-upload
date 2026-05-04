import json
import os
from loguru import logger

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "api_id": "",
    "api_hash": "",
    "chat_id": "",
    "telegram_bot_token": "",
    "upload_delay": 2,
    "caption_template": "{name}\n🎬 Episode {index}",
    "send_as_video": True,
}


def load_config() -> dict:
    """Load configuration from config.json. Returns defaults if file missing or corrupt."""
    try:
        if os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 0:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults so new keys are always present
            merged = {**DEFAULT_CONFIG, **data}
            logger.info("Configuration loaded successfully.")
            return merged
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read config.json, using defaults: {e}")
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> bool:
    """Save configuration dict to config.json. Returns True on success."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        logger.info("Configuration saved successfully.")
        return True
    except IOError as e:
        logger.error(f"Failed to save config.json: {e}")
        return False


def validate_config(config: dict) -> list[str]:
    """
    Validate critical config fields.
    Returns a list of human-readable error strings (empty = valid).
    """
    errors: list[str] = []

    api_id = str(config.get("api_id", "")).strip()
    if not api_id or not api_id.isdigit():
        errors.append("API ID must be a numeric value.")

    api_hash = str(config.get("api_hash", "")).strip()
    if not api_hash or len(api_hash) < 10:
        errors.append("API Hash appears invalid (too short).")

    chat_id = str(config.get("chat_id", "")).strip()
    if not chat_id:
        errors.append("Target Chat ID is required.")

    return errors
