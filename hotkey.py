"""Global hotkey using the Win32 RegisterHotKey API.

Windows notifies us only when this exact key combination is pressed; no other keystrokes
are ever visible to JARVIS (unlike keyboard-hook libraries, which see everything you type).
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading

log = logging.getLogger("jarvis.hotkey")

MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


def parse(combo):
    *mods, key = [p.strip().lower() for p in combo.split("+")]
    flags = 0
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"Unknown modifier '{m}' in hotkey '{combo}'")
        flags |= MODIFIERS[m]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key == "space":
        vk = 0x20
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError(f"Unsupported key '{key}' in hotkey '{combo}'")
    if not flags:
        raise ValueError("A hotkey needs at least one modifier, so normal typing isn't captured")
    return flags, vk


def start(combo, callback):
    """Registers the hotkey on a dedicated thread. Returns False if it couldn't be registered."""
    flags, vk = parse(combo)
    ready = threading.Event()
    ok = [False]

    def loop():
        user32 = ctypes.windll.user32
        ok[0] = bool(user32.RegisterHotKey(None, 1, flags | MOD_NOREPEAT, vk))
        ready.set()
        if not ok[0]:
            log.warning("Hotkey %s is already taken by another app", combo)
            return
        log.info("Hotkey %s registered", combo)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                callback()

    threading.Thread(target=loop, daemon=True, name="hotkey").start()
    ready.wait(2)
    return ok[0]
