"""Encrypted JSON files and plain local settings.

Encryption uses Windows DPAPI: only the same Windows user account on this PC can decrypt
the files. Copying them to another machine or account yields nothing.
"""
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os

import config

log = logging.getLogger("jarvis.store")

_ENTROPY = b"jarvis-memory-v1"  # extra secret mixed into the encryption key
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(func, data):
    blob_in, _keep_in = _to_blob(data)
    entropy, _keep_entropy = _to_blob(_ENTROPY)
    blob_out = _Blob()
    if not func(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def encrypt(data):
    return _dpapi(ctypes.windll.crypt32.CryptProtectData, data)


def decrypt(data):
    return _dpapi(ctypes.windll.crypt32.CryptUnprotectData, data)


def load(path, default):
    """Decrypts a JSON file. An unreadable file is moved aside, never overwritten."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "rb") as f:
            return json.loads(decrypt(f.read()))
    except Exception:
        backup = path + ".unreadable"
        os.replace(path, backup)
        log.exception("%s unreadable, moved to %s", path, backup)
        return default


def save(path, value):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(encrypt(json.dumps(value).encode("utf-8")))
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written file


# --- Plain settings (not secret, but personal, so kept out of the repo)

def get_setting(name, default=""):
    try:
        with open(config.SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f).get(name, default)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def set_setting(name, value):
    try:
        with open(config.SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data[name] = value
    with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
