"""JARVIS: say "Hey Jarvis" (or click the orb), then talk.

Flow: wake word -> record until silence -> Whisper -> Qwen (with tools) -> Kokoro.
After replying it listens a few more seconds for a follow-up without the wake word.
Saying "Hey Jarvis" or clicking the orb while it's talking interrupts it.
"""
import ctypes
import logging
import logging.handlers
import os
import re
import sys
import threading
import time

import config

# Under pythonw (no console) stdout/stderr are None, and libraries that print would crash.
if sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.stderr = open(os.devnull, "w")

handlers = [logging.handlers.RotatingFileHandler(config.LOG_FILE, maxBytes=1_000_000,
                                                 backupCount=2, encoding="utf-8")]
if sys.stdout.isatty():
    handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                    datefmt="%H:%M:%S", handlers=handlers)
log = logging.getLogger("jarvis")

# Without a console, uncaught errors (e.g. in menu actions) would vanish; log them instead.
sys.excepthook = lambda *exc: log.error("Unhandled error", exc_info=exc)
threading.excepthook = lambda args: log.error("Unhandled error in thread %s", args.thread.name,
                                              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

import audio  # noqa: E402
import autostart  # noqa: E402
import hotkey  # noqa: E402
import securestore  # noqa: E402
import spotify  # noqa: E402
import tools  # noqa: E402
from brain import Brain  # noqa: E402
from stt import STT  # noqa: E402
from tts import TTS  # noqa: E402
from ui import Overlay  # noqa: E402


class Jarvis:
    def __init__(self):
        self.ui = None  # Bridge, set once the overlay exists
        self.muted = False
        self.trigger = threading.Event()  # set when the orb is clicked
        self.sleep_request = threading.Event()  # "Sleep now" from the menu
        self.mic = self.tts = None
        self.asleep = False
        self.last_used = time.time()

    def set_state(self, state):
        self.ui.state.emit(state)

    def level(self, state):
        """Voice loudness for the orb animation, 0..1."""
        if state == "listening" and self.mic:
            return min(1.0, self.mic.level * 12)
        if state == "speaking" and self.tts:
            return min(1.0, self.tts.level() * 4)
        return 0.0

    def load(self):
        start = time.time()
        threading.Thread(target=tools.load_app_index, daemon=True).start()
        log.info("Loading speech recognition...")
        self.stt = STT()  # before the LLM so Ollama sizes its GPU offload around it
        log.info("Loading language model %s...", config.OLLAMA_MODEL)
        self.brain = Brain()
        self.brain.warm_up()
        log.info("Loading voice...")
        self.tts = TTS()
        self.wake = audio.WakeWord()
        self.mic = audio.Mic()
        self.recorder = audio.Recorder(self.mic)
        tools.notify = self.announce
        log.info("Ready in %.1fs. Say 'Hey Jarvis'.", time.time() - start)

    def announce(self, text):
        """Timers and reminders: chime, show on the overlay and speak."""
        audio.chime((990, 990))
        time.sleep(0.25)
        self.ui.notice.emit("Reminder" if text.startswith(("Reminder", "While")) else "JARVIS", text)
        self.tts.say(text)

    def run(self):
        try:
            self.load()
        except Exception:
            log.exception("Startup failed")
            self.set_state("error")
            return
        self.mic.start()
        self.tts.say("Online.")
        self.set_state("idle")
        while True:
            chunk = self.mic.read(timeout=0.2)
            triggered = self.trigger.is_set()
            if not triggered and chunk is not None and not self.muted:
                triggered = self.wake.detect(chunk)
            if triggered:
                self.wake.reset()
                if self.asleep:
                    self.wake_up()
                self.conversation()
                self.last_used = time.time()
                self.set_state("sleeping" if self.asleep else "idle")
            elif not self.asleep and (self.sleep_request.is_set() or self._idle_too_long()):
                self.go_to_sleep()
            self.sleep_request.clear()

    # --- idle mode: models leave the GPU; the wake word, voice and reminders keep working

    def _idle_too_long(self):
        limit = config.IDLE_AFTER_MINUTES * 60
        return limit > 0 and time.time() - self.last_used > limit and not self.tts.busy()

    def go_to_sleep(self):
        log.info("Going to sleep: unloading models from the GPU")
        self.asleep = True
        self.set_state("sleeping")
        self.brain.unload()
        self.stt.unload()

    def wake_up(self):
        """Reload both models in parallel, in the background, while the user is still talking."""
        log.info("Waking up: reloading models")
        self.asleep = False
        t0 = time.time()
        threading.Thread(target=self.stt.load, daemon=True, name="wake-stt").start()

        def reload_llm():
            self.brain.warm_up()
            log.info("Models reloaded in %.1fs", time.time() - t0)

        threading.Thread(target=reload_llm, daemon=True, name="wake-llm").start()

    def conversation(self):
        timeout = config.START_TIMEOUT
        while True:
            self.trigger.clear()
            self.tts.stop()
            audio.chime()
            self.set_state("listening")
            recording = self.recorder.record(timeout)
            if recording is None:
                return
            self.set_state("thinking")
            t0 = time.time()
            text = self.stt.transcribe(recording)
            if not text:
                return
            log.info("you: %s  (stt %.2fs)", text if config.LOG_CONVERSATIONS else f"({len(text)} chars)",
                     time.time() - t0)
            self.ui.user.emit(text)

            interrupted = self.respond(text)
            self.mic.drain()
            self.wake.reset()
            if not interrupted:
                time.sleep(0.2)  # let the speaker's echo die out
                self.mic.drain()
                timeout = config.FOLLOWUP_TIMEOUT
            else:
                timeout = config.START_TIMEOUT

    def respond(self, text):
        """Runs the LLM while watching for the wake word. Returns True if interrupted."""
        cancel = threading.Event()
        first = []

        def on_sentence(sentence):
            if not first:
                first.append(time.time())
                self.set_state("speaking")
            self.ui.reply.emit(sentence)
            self.tts.say(sentence)

        t0 = time.time()
        worker = threading.Thread(target=self.brain.ask, args=(text, on_sentence, cancel), daemon=True)
        worker.start()
        while worker.is_alive() or self.tts.busy():
            chunk = self.mic.read(timeout=0.1)
            if self.trigger.is_set() or (chunk is not None and self.wake.detect(chunk)):
                log.info("interrupted")
                cancel.set()
                self.tts.stop()
                worker.join(timeout=2)
                return True
        if first:
            log.info("first audio after %.2fs", first[0] - t0)
        return False


SPOTIFY_HELP = """To connect Spotify (one time, about 2 minutes):

1. Open developer.spotify.com/dashboard and log in.
2. Click "Create app". Any name and description.
3. Redirect URI: http://127.0.0.1:8888/callback  (click Add)
4. Tick "Web API", accept the terms, Save.
5. Copy the Client ID from the app's page and paste it below.

No client secret is needed. Your browser will then ask you to approve access."""


def ask_text(title, label, value=""):
    """Text prompt that comes to the front. JARVIS has no main window, so a plain dialog can
    open behind whatever app is focused."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog
    dialog = QInputDialog()
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(value)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint)
    dialog.resize(520, dialog.sizeHint().height())
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    ok = dialog.exec() == QInputDialog.Accepted
    return dialog.textValue(), ok


def connect_spotify(bridge):
    log.info("Connect Spotify dialog opened")
    cid, ok = ask_text("Connect Spotify", SPOTIFY_HELP, spotify.client_id())
    cid = cid.strip()
    if not ok or not cid:
        log.info("Connect Spotify cancelled")
        return
    if not re.fullmatch(r"[0-9a-fA-F]{32}", cid):
        bridge.notice.emit("Spotify", "That doesn't look like a Client ID (32 letters and digits).")
        return
    securestore.set_setting("spotify_client_id", cid)
    bridge.notice.emit("Spotify", "Approve access in your browser...")

    def done(success, message):
        log.info("Spotify connect: %s", message)
        bridge.notice.emit("Spotify", message)

    spotify.connect(done)


def set_home_city(bridge):
    city, ok = ask_text(
        "Home city", "City for weather when you don't name one\n(e.g. \"Casablanca\" or \"Paris, France\"):",
        securestore.get_setting("home_city"))
    if ok:
        securestore.set_setting("home_city", " ".join(city.split())[:80])
        bridge.notice.emit("Weather", f"Home city set to {city.strip()}." if city.strip() else "Home city cleared.")


def already_running():
    """Named mutex so a second copy (e.g. autostart + manual launch) exits instead of
    fighting over the microphone. Kept alive for the life of the process."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    main.mutex = kernel32.CreateMutexW(None, False, "Local\\JARVIS-assistant")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main():
    os.chdir(config.ROOT)
    if already_running():
        log.info("JARVIS is already running; exiting")
        return
    jarvis = Jarvis()

    def on_pause(paused):
        jarvis.muted = paused
        log.info("wake word %s", "paused" if paused else "live")

    def on_autostart(enabled):
        autostart.set_enabled(enabled)
        log.info("start with Windows %s", "on" if enabled else "off")

    hotkey_ok = hotkey.start(config.HOTKEY, jarvis.trigger.set)
    overlay = Overlay(on_click=jarvis.trigger.set, on_pause_toggle=on_pause,
                      on_quit=lambda: os._exit(0), level_fn=jarvis.level,
                      autostart_enabled=autostart.is_enabled(), on_autostart_toggle=on_autostart,
                      on_clear_memory=tools.memory.clear,
                      hotkey_label=config.HOTKEY.title() if hotkey_ok else "",
                      extra_actions=[("Sleep now", jarvis.sleep_request.set),
                                     ("Connect Spotify...", lambda: connect_spotify(overlay.bridge)),
                                     ("Set home city...", lambda: set_home_city(overlay.bridge))])
    jarvis.ui = overlay.bridge
    threading.Thread(target=jarvis.run, daemon=True).start()
    sys.exit(overlay.run())


if __name__ == "__main__":
    main()
