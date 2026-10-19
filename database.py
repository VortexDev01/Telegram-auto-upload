"""
Local SQLite storage.

Keeps settings and upload history in a per-user data folder so they survive
restarts. This matters for the packaged EXE: PyInstaller unpacks it into a
temporary folder that is deleted on exit, so anything saved next to the code
(the old config.json) was lost every time the app closed.
"""

import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import closing
from typing import Any

from loguru import logger

APP_NAME = "AutoUpload"


def _get_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


DATA_DIR = _get_data_dir()
DB_FILE = os.path.join(DATA_DIR, "autoupload.db")
LOG_DIR = os.path.join(DATA_DIR, "logs")

_schema_lock = threading.Lock()
_schema_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS upload_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_key    TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    file_size   INTEGER NOT NULL,
    file_mtime  INTEGER NOT NULL,
    new_name    TEXT    NOT NULL,
    message_id  INTEGER,
    uploaded_at REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_lookup
    ON upload_history (chat_key, file_path, file_size, file_mtime);
"""


def _connect() -> sqlite3.Connection:
    global _schema_ready
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    if not _schema_ready:
        with _schema_lock:
            if not _schema_ready:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                conn.commit()
                _schema_ready = True
                _migrate_legacy_files(conn)
    return conn


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------

def get_all_settings() -> dict:
    """Return every stored setting as {key: value} with original types."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            result[row["key"]] = row["value"]
    return result


def set_settings(values: dict[str, Any]) -> None:
    """Insert or update several settings in one transaction."""
    now = time.time()
    with closing(_connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [(k, json.dumps(v, ensure_ascii=False), now) for k, v in values.items()],
        )


# ----------------------------------------------------------------------
# Upload history (used to skip files that were already sent)
# ----------------------------------------------------------------------

def file_signature(path: str) -> tuple[str, int, int] | None:
    """(normalized path, size, mtime) identifying one version of a file."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return os.path.normcase(os.path.abspath(path)), st.st_size, int(st.st_mtime)


def record_upload(chat_key: str, path: str, new_name: str, message_id: int | None) -> None:
    sig = file_signature(path)
    if sig is None:
        return
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO upload_history "
            "(chat_key, file_path, file_size, file_mtime, new_name, message_id, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_key, *sig, new_name, message_id, time.time()),
        )


def get_uploaded_signatures(chat_key: str) -> set[tuple[str, int, int]]:
    """All (path, size, mtime) already uploaded to *chat_key*."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT file_path, file_size, file_mtime FROM upload_history WHERE chat_key = ?",
            (chat_key,),
        ).fetchall()
    return {(r["file_path"], r["file_size"], r["file_mtime"]) for r in rows}


def clear_history(chat_key: str | None = None) -> int:
    """Delete upload history (for one chat, or all). Returns rows removed."""
    with closing(_connect()) as conn, conn:
        if chat_key is None:
            cur = conn.execute("DELETE FROM upload_history")
        else:
            cur = conn.execute("DELETE FROM upload_history WHERE chat_key = ?", (chat_key,))
        return cur.rowcount


# ----------------------------------------------------------------------
# One-time migration from the old config.json / session files
# ----------------------------------------------------------------------

def _legacy_dirs() -> list[str]:
    """Folders where older versions kept config.json and session files."""
    candidates = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.dirname(sys.executable))
    seen, result = set(), []
    for d in candidates:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen and key != os.path.normcase(DATA_DIR):
            seen.add(key)
            result.append(d)
    return result


def _migrate_legacy_files(conn: sqlite3.Connection) -> None:
    has_settings = conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone() is not None

    for folder in _legacy_dirs():
        legacy_config = os.path.join(folder, "config.json")
        if not has_settings and os.path.isfile(legacy_config):
            try:
                with open(legacy_config, "r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                with conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                        [(k, json.dumps(v, ensure_ascii=False), now) for k, v in data.items()],
                    )
                has_settings = True
                logger.info(f"Imported settings from {legacy_config}")
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not import {legacy_config}: {e}")

        # A user-account login cannot be redone without a console, so keep it.
        legacy_session = os.path.join(folder, "autoupload_session.session")
        target_session = os.path.join(DATA_DIR, "autoupload_session.session")
        if os.path.isfile(legacy_session) and not os.path.exists(target_session):
            try:
                shutil.copy2(legacy_session, target_session)
                if os.path.isfile(legacy_session + "-journal"):
                    shutil.copy2(legacy_session + "-journal", target_session + "-journal")
                logger.info(f"Copied Telegram login session from {folder}")
            except OSError as e:
                logger.warning(f"Could not copy session file: {e}")
