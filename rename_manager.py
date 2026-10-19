"""
Rename Manager
Generates new display names for files based on a user-defined pattern.
"""

import os


def generate_name(base_name: str, index: int, extension: str) -> str:
    """
    Create a renamed filename.
    Example: generate_name("Kang Movie", 1, ".mp4") → "Kang Movie 1.mp4"
    """
    return f"{base_name} {index}{extension}"


def generate_caption(template: str, base_name: str, index: int) -> str:
    """
    Fill a caption template with placeholders.
    Supported placeholders: {name}, {index}
    Example template: "{name}\n🎬 Episode {index}"
    """
    try:
        return template.format(name=f"{base_name} {index}", index=index)
    except (KeyError, IndexError, ValueError):
        return f"{base_name} {index}"


def preview_renames(files: list[str], base_name: str) -> list[dict]:
    """
    Given a list of original file paths and a base name, return a preview list.
    Each entry: {"original": ..., "new_name": ..., "index": ...}
    """
    previews: list[dict] = []
    for idx, filepath in enumerate(files, start=1):
        ext = os.path.splitext(filepath)[1]
        new_name = generate_name(base_name, idx, ext)
        previews.append({
            "original": os.path.basename(filepath),
            "new_name": new_name,
            "index": idx,
            "path": filepath,
        })
    return previews
