"""Clipboard and file tools, restricted to the user's own folders and safe file types."""
import ctypes
import ctypes.wintypes as wt
import datetime
import difflib
import logging
import os
import re

import config

log = logging.getLogger("jarvis.files")

HOME = os.path.expanduser("~")
SEARCH_DIRS = [os.path.join(HOME, d) for d in ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Music")]
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "AppData", "$RECYCLE.BIN"}

TYPES = {
    "document": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "archive": {".zip", ".rar", ".7z"},
}
OPENABLE = set().union(*TYPES.values())  # never executables, scripts, shortcuts or installers
READABLE_TEXT = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".html", ".xml", ".ini", ".yaml", ".yml"}
SECRET_NAMES = re.compile(r"(^\.env|\.pem$|\.key$|\.kdbx$|\.pfx$|\.p12$|^id_(rsa|ed25519|ecdsa)|password|secret|credential)", re.I)

_last_results = []  # paths from the latest search, so the user can say "open the second one"


def _allowed(path):
    """Inside the user's profile, outside AppData and hidden/dot folders, not a secrets file."""
    real = os.path.realpath(path)
    if os.path.commonpath([real, os.path.realpath(HOME)]) != os.path.realpath(HOME):
        return False
    parts = os.path.relpath(real, HOME).split(os.sep)
    if parts[0] == "AppData" or any(p.startswith(".") for p in parts):
        return False
    if real.startswith(os.path.realpath(config.DATA_DIR)):
        return False
    return not SECRET_NAMES.search(os.path.basename(real))


# ---------------------------------------------------------------- clipboard

CF_UNICODETEXT = 13
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetClipboardData.restype = wt.HANDLE
_kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
_kernel32.GlobalAlloc.restype = wt.HGLOBAL
_user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
_user32.SetClipboardData.restype = wt.HANDLE


def _open_clipboard():
    for _ in range(10):  # another app may hold it briefly
        if _user32.OpenClipboard(None):
            return True
        ctypes.windll.kernel32.Sleep(50)
    return False


def read_clipboard() -> str:
    """Read the text the user has copied, e.g. to summarize, translate or explain it."""
    if not _open_clipboard():
        return "The clipboard is busy. Try again."
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return "The clipboard doesn't contain text."
        ptr = _kernel32.GlobalLock(handle)
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()
    if len(text) > config.MAX_READ_CHARS:
        text = text[:config.MAX_READ_CHARS] + "\n[truncated]"
    return text or "The clipboard is empty."


def copy_to_clipboard(text: str) -> str:
    """Put text on the clipboard so the user can paste it.

    Args:
        text: The text to copy.
    """
    data = (str(text) + "\0").encode("utf-16-le")
    handle = _kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    ptr = _kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    _kernel32.GlobalUnlock(handle)
    if not _open_clipboard():
        return "The clipboard is busy. Try again."
    try:
        _user32.EmptyClipboard()
        _user32.SetClipboardData(CF_UNICODETEXT, handle)  # the clipboard now owns the memory
    finally:
        _user32.CloseClipboard()
    return "Copied to the clipboard."


# ---------------------------------------------------------------- files

def find_files(name: str = "", file_type: str = "any", modified_within_days: int = 0) -> str:
    """Find files on the PC (Desktop, Downloads, Documents, Pictures, Videos, Music), e.g. the
    latest screenshot or a downloaded PDF. Returns a numbered list, best match first.

    Args:
        name: Words from the file name; leave empty to just get the newest files.
        file_type: One of "any", "document", "image", "video", "audio", "archive".
        modified_within_days: Only files changed in the last N days; 0 means no limit.
    """
    exts = TYPES.get(file_type)
    cutoff = 0
    if modified_within_days:
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=int(modified_within_days))).timestamp()
    words = [w for w in re.split(r"\W+", name.lower()) if w]
    found = []
    for root_dir in SEARCH_DIRS:
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in OPENABLE or (exts and ext not in exts):
                    continue
                path = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                stem = os.path.splitext(f)[0].lower()
                if words:
                    score = sum(w in stem for w in words) / len(words)
                    if score < 1:
                        score = max(score, difflib.SequenceMatcher(None, " ".join(words), stem).ratio())
                    if score < 0.6:
                        continue
                else:
                    score = 1
                if _allowed(path):
                    found.append((score, mtime, path))
            if len(found) > 5000:
                break
    found.sort(key=lambda x: (-x[0], -x[1]))
    _last_results[:] = [p for _, _, p in found[:8]]
    if not _last_results:
        return "No matching files found."
    lines = []
    for i, p in enumerate(_last_results, 1):
        when = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%b %d %H:%M")
        lines.append(f"{i}. {os.path.basename(p)} (in {os.path.basename(os.path.dirname(p))}, {when})")
    return "\n".join(lines)


def _resolve(file):
    file = str(file).strip()
    if file.isdigit() and 1 <= int(file) <= len(_last_results):
        return _last_results[int(file) - 1]
    for p in _last_results:
        if os.path.basename(p).lower() == file.lower():
            return p
    return file if os.path.isabs(file) else None


def open_file(file: str) -> str:
    """Open a file from the latest find_files results with its default app.

    Args:
        file: The result number (e.g. "1") or the exact file name from find_files.
    """
    path = _resolve(file)
    if not path or not os.path.isfile(path):
        return "Run find_files first, then open a result by its number."
    if os.path.splitext(path)[1].lower() not in OPENABLE or not _allowed(path):
        return "For safety I only open documents, images and media from your own folders."
    os.startfile(path)
    return f"Opened {os.path.basename(path)}."


def read_file(file: str) -> str:
    """Read a text or PDF file from the latest find_files results, e.g. to summarize it.

    Args:
        file: The result number (e.g. "1") or the exact file name from find_files.
    """
    path = _resolve(file)
    if not path or not os.path.isfile(path):
        return "Run find_files first, then read a result by its number."
    if not _allowed(path):
        return "That file is outside the folders I'm allowed to read."
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        text = ""
        for page in PdfReader(path).pages[:30]:
            text += (page.extract_text() or "") + "\n"
            if len(text) > config.MAX_READ_CHARS:
                break
    elif ext in READABLE_TEXT:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read(config.MAX_READ_CHARS + 1)
    else:
        return "I can only read text and PDF files."
    text = text.strip()
    if len(text) > config.MAX_READ_CHARS:
        text = text[:config.MAX_READ_CHARS] + "\n[truncated]"
    return text or "The file has no readable text."
