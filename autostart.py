"""Start JARVIS at sign-in via the per-user Run key (no admin rights needed)."""
import os
import winreg

import config

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "JARVIS"


def command():
    # Both paths quoted: unquoted paths with spaces can be hijacked by planted executables.
    pythonw = os.path.join(config.ROOT, ".venv", "Scripts", "pythonw.exe")
    return f'"{pythonw}" "{os.path.join(config.ROOT, "main.py")}"'


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value, _ = winreg.QueryValueEx(key, NAME)
            return value == command()
    except FileNotFoundError:
        return False


def set_enabled(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass
