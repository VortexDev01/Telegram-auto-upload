"""
Encrypt secrets (API hash, bot token) before they are written to disk.

On Windows this uses DPAPI, which ties the data to the current Windows user:
another account, or the database copied to another PC, can't read it. Other
platforms keep the value as-is.
"""

import base64
import ctypes
import sys

PREFIX = "dpapi:"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _call(func, data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1: never show a prompt.
    if not func(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt(value: str) -> str:
    if sys.platform != "win32" or not value:
        return value
    crypt32 = ctypes.windll.crypt32
    data = _call(crypt32.CryptProtectData, value.encode("utf-8"))
    return PREFIX + base64.b64encode(data).decode("ascii")


def decrypt(value: str) -> str:
    """Decrypt a value from encrypt(); plain values (older versions) pass through."""
    if not isinstance(value, str) or not value.startswith(PREFIX) or sys.platform != "win32":
        return value
    crypt32 = ctypes.windll.crypt32
    data = _call(crypt32.CryptUnprotectData, base64.b64decode(value[len(PREFIX):]))
    return data.decode("utf-8")
