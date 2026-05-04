"""
File Manager
Handles scanning folders, filtering video files, and sorting them naturally.
"""

import os
from natsort import natsorted
from loguru import logger

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
        "size_mb": round(size_mb, 2),
        "extension": os.path.splitext(filepath)[1].lower(),
    }
