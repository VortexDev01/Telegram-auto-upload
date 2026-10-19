"""
File Manager
Handles scanning folders, filtering video files, and sorting them naturally.
"""

import os
from natsort import natsorted
from loguru import logger

import database

# Supported video extensions
VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".ts", ".mpeg", ".mpg", ".3gp",
)


def get_sorted_videos(folder: str) -> list[str]:
    """
    Scan *folder* for video files and return them naturally sorted.
    Returns a list of absolute paths.
    """
    if not os.path.isdir(folder):
        logger.error(f"Folder does not exist: {folder}")
        return []

    files: list[str] = []
    for entry in os.scandir(folder):
        if entry.is_file() and entry.name.lower().endswith(VIDEO_EXTENSIONS):
            files.append(entry.path)

    # Natural sort so "Episode 2" comes before "Episode 10"
    sorted_files = natsorted(files, key=lambda p: os.path.basename(p).lower())
    logger.info(f"Found {len(sorted_files)} video(s) in '{folder}'.")
    return sorted_files


def get_file_info(filepath: str) -> dict:
    """Return basic metadata about a single file."""
    stat = os.stat(filepath)
    size_mb = stat.st_size / (1024 * 1024)
    return {
        "path": filepath,
        "name": os.path.basename(filepath),
        "size": stat.st_size,
        "size_mb": round(size_mb, 2),
        "extension": os.path.splitext(filepath)[1].lower(),
    }


# Telegram limits: 2000 MiB normally, 4000 MiB for Premium user accounts.
MAX_UPLOAD_BYTES = 2000 * 1024 * 1024
MAX_PREMIUM_UPLOAD_BYTES = 4000 * 1024 * 1024

# Check statuses
STATUS_READY = "ready"
STATUS_UPLOADED = "uploaded"
STATUS_MISSING = "missing"
STATUS_EMPTY = "empty"
STATUS_TOO_LARGE = "too_large"
STATUS_PREMIUM_ONLY = "premium_only"

BLOCKING_STATUSES = (STATUS_MISSING, STATUS_EMPTY, STATUS_TOO_LARGE)


def check_file(filepath: str, uploaded: set, using_bot: bool) -> str:
    """
    Pre-upload check for one file.
    *uploaded* is the set of signatures from database.get_uploaded_signatures().
    """
    sig = database.file_signature(filepath)
    if sig is None:
        return STATUS_MISSING
    size = sig[1]
    if size == 0:
        return STATUS_EMPTY
    if size > MAX_PREMIUM_UPLOAD_BYTES or (using_bot and size > MAX_UPLOAD_BYTES):
        return STATUS_TOO_LARGE
    if sig in uploaded:
        return STATUS_UPLOADED
    if size > MAX_UPLOAD_BYTES:
        return STATUS_PREMIUM_ONLY
    return STATUS_READY
