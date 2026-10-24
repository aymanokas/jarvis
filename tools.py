"""Actions JARVIS can take on the PC.

Every function in TOOLS is handed to the LLM; its type hints and docstring become the
tool schema, so keep docstrings short and precise.
"""
import ctypes
import datetime
import difflib
import json
import logging
import re
import subprocess
import threading
import urllib.parse
import urllib.request
import webbrowser

import psutil

import config

log = logging.getLogger("jarvis.tools")
NO_WINDOW = subprocess.CREATE_NO_WINDOW

# Set by main so timers can speak when they fire.
notify = lambda text: None  # noqa: E731

# ---------------------------------------------------------------- app index

_apps = {}  # lowercase display name -> (display name, AppID)

SPECIAL_APPS = {
    "settings": "ms-settings:",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "control panel": "control.exe",
}


def load_app_index():
    """Everything in the Start menu, including Store apps."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
        ).stdout
        for app in json.loads(out):
            name = app["Name"]
            if "uninstall" not in name.lower():
                _apps[name.lower()] = (name, app["AppID"])
        log.info("Indexed %d apps", len(_apps))
    except Exception:
        log.exception("Could not index Start menu apps")


def _find_app(query):
    q = query.lower().strip()
    if q in _apps:
        return _apps[q]
    containing = [k for k in _apps if q in k]
    if containing:
        return _apps[min(containing, key=len)]
    close = difflib.get_close_matches(q, _apps.keys(), n=1, cutoff=0.6)
    return _apps[close[0]] if close else None


# ---------------------------------------------------------------- tools

def open_app(name: str) -> str:
    """Open an application installed on this PC.

    Args:
        name: The app's name, e.g. "chrome", "spotify", "calculator", "settings".
    """
    q = name.lower().strip()
    if q in SPECIAL_APPS:
        subprocess.Popen(["cmd", "/c", "start", "", SPECIAL_APPS[q]], creationflags=NO_WINDOW)
        return f"Opened {name}."
    app = _find_app(q)
    if not app:
        return f"No installed app matching '{name}' was found."
    display, app_id = app
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
    return f"Opened {display}."


PROCESS_ALIASES = {
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox", "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
    "vs code": "code", "visual studio code": "code", "notepad": "notepad", "spotify": "spotify",
    "discord": "discord", "steam": "steam", "calculator": "calculatorapp",
}
PROTECTED = {
    "system", "svchost", "csrss", "winlogon", "wininit", "lsass", "services", "smss", "dwm",
    "explorer", "python", "pythonw", "ollama", "ollama app", "registry", "fontdrvhost",
}


def close_app(name: str) -> str:
    """Close a running application.

    Args:
        name: The app's name, e.g. "chrome", "spotify", "notepad".
    """
    q = name.lower().strip()
    target = PROCESS_ALIASES.get(q, q.replace(" ", ""))
    procs = {}
    for p in psutil.process_iter(["name"]):
        pname = (p.info["name"] or "").lower().removesuffix(".exe")
        procs.setdefault(pname, []).append(p)
    if target not in procs:
        close = difflib.get_close_matches(target, procs.keys(), n=1, cutoff=0.75)
        if not close:
            return f"'{name}' doesn't appear to be running."
        target = close[0]
    if target in PROTECTED:
        return f"Refusing to close {target}; it's a system process."
    for p in procs[target]:
        try:
            p.terminate()
        except psutil.Error:
            pass
    return f"Closed {target}."


def open_website(url: str) -> str:
    """Open a website in the default browser.

    Args:
        url: The address, e.g. "github.com" or "https://example.com".
    """
    if not re.match(r"^https?://", url):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}."


def search_web(query: str) -> str:
    """Search the web in the default browser.

    Args:
        query: What to search for.
    """
    webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return f"Searching for {query}."


def play_youtube(query: str) -> str:
    """Find a video or music on YouTube and start playing it.

    Args:
        query: What to play, e.g. "lofi hip hop" or "Bohemian Rhapsody Queen".
    """
    search = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    try:
        req = urllib.request.Request(search, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore")
        match = re.search(r'"videoId":"([\w-]{11})"', html)
        if match:
            webbrowser.open(f"https://www.youtube.com/watch?v={match.group(1)}")
            return f"Playing {query} on YouTube."
    except Exception as e:
        log.warning("YouTube lookup failed: %s", e)
    webbrowser.open(search)
    return f"Opened YouTube results for {query}."


_MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


def media_control(action: str) -> str:
    """Control whatever media is playing (Spotify, YouTube, etc.).

    Args:
        action: One of "play_pause", "next", "previous", "stop".
    """
    key = _MEDIA_KEYS.get(action)
    if key is None:
        return f"Unknown media action '{action}'."
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)
    return f"Media {action.replace('_', '/')}."


def _endpoint_volume():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    device = AudioUtilities.GetSpeakers()
    if hasattr(device, "EndpointVolume"):
        return device.EndpointVolume
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def set_volume(level: int) -> str:
    """Set the system volume to an exact level.

    Args:
        level: Volume from 0 to 100.
    """
    level = max(0, min(100, int(level)))
    vol = _endpoint_volume()
    vol.SetMute(0, None)
    vol.SetMasterVolumeLevelScalar(level / 100, None)
    return f"Volume set to {level} percent."


def adjust_volume(change: int) -> str:
    """Turn the system volume up or down relative to its current level.

    Args:
        change: Percentage points to add, e.g. 10 for louder or -10 for quieter.
    """
    vol = _endpoint_volume()
    current = round(vol.GetMasterVolumeLevelScalar() * 100)
    return set_volume(current + int(change))


def mute_audio(mute: bool) -> str:
    """Mute or unmute the system audio.

    Args:
        mute: True to mute, False to unmute.
    """
    _endpoint_volume().SetMute(1 if mute else 0, None)
    return "Muted." if mute else "Unmuted."


def set_timer(seconds: int, label: str = "timer") -> str:
    """Set a timer that announces itself when done.

    Args:
        seconds: Duration in seconds (convert minutes/hours to seconds).
        label: Short name for the timer, e.g. "pasta".
    """
    seconds = int(seconds)
    threading.Timer(seconds, lambda: notify(f"Your {label} timer is done.")).start()
    mins, secs = divmod(seconds, 60)
    span = f"{mins} minute{'s' * (mins != 1)}" if mins else ""
    span += f"{' and ' if mins and secs else ''}{secs} second{'s' * (secs != 1)}" if secs else ""
    return f"{label.capitalize()} timer set for {span}."


def take_note(text: str) -> str:
    """Save a note to the user's notes file.

    Args:
        text: The note's content.
    """
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(config.NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {text}\n")
    return "Noted."


def get_system_status() -> str:
    """Report CPU, memory, GPU and battery usage of this PC."""
    parts = [f"CPU {psutil.cpu_percent(interval=0.3):.0f}%",
             f"RAM {psutil.virtual_memory().percent:.0f}%"]
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
        ).stdout.strip()
        util, used, total, temp = [x.strip() for x in out.split(",")]
        parts.append(f"GPU {util}% load, {used} of {total} MB VRAM, {temp}°C")
    except Exception:
        pass
    battery = psutil.sensors_battery()
    if battery:
        parts.append(f"battery {battery.percent:.0f}%")
    return ", ".join(parts)


def lock_computer() -> str:
    """Lock the Windows session."""
    ctypes.windll.user32.LockWorkStation()
    return "Locked."


TOOLS = [open_app, close_app, open_website, search_web, play_youtube, media_control,
         set_volume, adjust_volume, mute_audio, set_timer, take_note, get_system_status,
         lock_computer]
TOOL_MAP = {f.__name__: f for f in TOOLS}


def run(name, args):
    func = TOOL_MAP.get(name)
    if not func:
        return f"Unknown tool {name}."
    try:
        result = func(**(args or {}))
    except Exception as e:
        log.exception("Tool %s failed", name)
        result = f"Error: {e}"
    log.info("tool %s(%s) -> %s", name, args, result)
    return result
