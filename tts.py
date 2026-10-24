"""Text-to-speech with Kokoro. Synthesis and playback run in separate threads so the
next sentence is being generated while the current one plays."""
import logging
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd
from kokoro_onnx import Kokoro

import config

log = logging.getLogger("jarvis.tts")


SPOKEN = [
    (r"(\d)\s*°\s*C\b", r"\1 degrees"),
    (r"(\d)\s*°\s*F\b", r"\1 degrees Fahrenheit"),
    (r"(\d)\s*%", r"\1 percent"),
    (r"(\d)\s*GB\b", r"\1 gigabytes"),
    (r"(\d)\s*MB\b", r"\1 megabytes"),
    (r"(\d)\s*km\b", r"\1 kilometers"),
    (r"\bvs\.?\s", "versus "),
    (r"\be\.g\.\s?", "for example, "),
    (r"\bi\.e\.\s?", "that is, "),
    (r"\s&\s", " and "),
]

FADE = 0.012  # seconds of fade in/out, avoids clicks between sentences
GAP = 0.09    # breath between chunks


def clean(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|~]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿️‍]", "", text)  # emoji
    for pattern, repl in SPOKEN:
        text = re.sub(pattern, repl, text, flags=re.I)
    return " ".join(text.split())


def _soften(samples, sr):
    """Fade the edges and add a short breath of silence after."""
    n = min(int(sr * FADE), len(samples) // 2)
    if n:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        samples[:n] *= ramp
        samples[-n:] *= ramp[::-1]
    return np.concatenate([samples, np.zeros(int(sr * GAP), np.float32)])


class TTS:
    def __init__(self):
        self.kokoro = Kokoro(config.KOKORO_MODEL, config.KOKORO_VOICES)
        self.text_q = queue.Queue()
        self.audio_q = queue.Queue()
        self._generation = 0  # bumped on stop() so queued stale sentences get dropped
        self._pending = 0
        self._playing = None  # (samples, sample_rate, start_time) of the current sentence
        self._lock = threading.Lock()
        self._synth("Ready.")  # warm-up
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _synth(self, text):
        return self.kokoro.create(text, voice=config.VOICE, speed=config.VOICE_SPEED, lang=config.VOICE_LANG)

    def say(self, text):
        text = clean(text)
        if not text:
            return
        with self._lock:
            self._pending += 1
        self.text_q.put((self._generation, text))

    def busy(self):
        return self._pending > 0

    def stop(self):
        self._generation += 1
        sd.stop()

    def _done(self):
        with self._lock:
            self._pending -= 1

    def _synth_loop(self):
        while True:
            gen, text = self.text_q.get()
            if gen != self._generation:
                self._done()
                continue
            try:
                samples, sr = self._synth(text)
                self.audio_q.put((gen, _soften(samples.astype(np.float32), sr), sr))
            except Exception:
                log.exception("TTS failed for %r", text)
                self._done()

    def _play_loop(self):
        while True:
            gen, samples, sr = self.audio_q.get()
            if gen == self._generation:
                self._playing = (samples, sr, time.time())
                sd.play(samples, sr)
                sd.wait()
                self._playing = None
            self._done()

    def level(self):
        """Loudness of what's playing right now, 0..1, read by the overlay."""
        playing = self._playing
        if not playing:
            return 0.0
        samples, sr, start = playing
        i = int((time.time() - start) * sr)
        window = samples[max(0, i - 512):i + 512]
        return float(np.sqrt(np.mean(window ** 2))) if len(window) else 0.0
