"""JARVIS: say "Hey Jarvis" (or click the orb), then talk.

Flow: wake word -> record until silence -> Whisper -> Qwen (with tools) -> Kokoro.
After replying it listens a few more seconds for a follow-up without the wake word.
Saying "Hey Jarvis" or clicking the orb while it's talking interrupts it.
"""
import ctypes
import logging
import logging.handlers
import os
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

import audio  # noqa: E402
import autostart  # noqa: E402
import hotkey  # noqa: E402
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
        self.mic = self.tts = None

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
        audio.chime((990, 990))
        time.sleep(0.25)
        self.ui.reply.emit(text)
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
                self.conversation()
                self.set_state("idle")

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
            log.info("you: %s  (stt %.2fs)", text, time.time() - t0)
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
                      hotkey_label=config.HOTKEY.title() if hotkey_ok else "")
    jarvis.ui = overlay.bridge
    threading.Thread(target=jarvis.run, daemon=True).start()
    sys.exit(overlay.run())


if __name__ == "__main__":
    main()
