"""Microphone capture, wake-word detection and utterance recording."""
import os
import queue
from collections import deque

import numpy as np
import openwakeword
import sounddevice as sd
import webrtcvad
from openwakeword.model import Model

import config


class Mic:
    """Continuously captures 80 ms int16 chunks into a queue."""

    def __init__(self):
        self.q = queue.Queue(maxsize=500)
        self.level = 0.0  # RMS of the latest chunk, 0..1, read by the overlay
        self.stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=config.CHUNK, device=config.INPUT_DEVICE, callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status):
        self.level = float(np.sqrt(np.mean((indata[:, 0] / 32768.0) ** 2)))
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def start(self):
        self.stream.start()

    def read(self, timeout=None):
        """Next chunk, or None on timeout."""
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break


class WakeWord:
    def __init__(self):
        path = os.path.join(os.path.dirname(openwakeword.__file__),
                            "resources", "models", "hey_jarvis_v0.1.onnx")
        self.model = Model(wakeword_models=[path], inference_framework="onnx")

    def detect(self, chunk):
        scores = self.model.predict(chunk)
        return max(scores.values()) >= config.WAKE_THRESHOLD

    def reset(self):
        self.model.reset()


class Recorder:
    """Records one utterance: waits for speech, stops after trailing silence."""

    FRAME_BYTES = 640  # 20 ms of 16 kHz int16, a size webrtcvad accepts

    def __init__(self, mic):
        self.mic = mic
        self.vad = webrtcvad.Vad(config.VAD_AGGRESSIVENESS)

    def _is_speech(self, chunk):
        data = chunk.tobytes()
        hits = sum(self.vad.is_speech(data[i:i + self.FRAME_BYTES], config.SAMPLE_RATE)
                   for i in range(0, len(data) - self.FRAME_BYTES + 1, self.FRAME_BYTES))
        return hits >= 2

    def record(self, start_timeout):
        """Returns float32 audio, or None if nobody spoke."""
        chunk_s = config.CHUNK / config.SAMPLE_RATE
        pre_roll = deque(maxlen=4)  # keep ~300 ms before speech so the first word isn't clipped
        frames, started = [], False
        waited = total = silence = voiced = 0.0

        while True:
            chunk = self.mic.read(timeout=1.0)
            if chunk is None:
                continue
            speech = self._is_speech(chunk)
            if not started:
                pre_roll.append(chunk)
                waited += chunk_s
                if speech:
                    started = True
                    frames.extend(pre_roll)
                elif waited >= start_timeout:
                    return None
                continue

            frames.append(chunk)
            total += chunk_s
            if speech:
                voiced += chunk_s
                silence = 0.0
            else:
                silence += chunk_s
            if silence >= config.END_SILENCE or total >= config.MAX_RECORD:
                break

        if voiced < 0.25:  # a click or a cough, not a sentence
            return None
        return np.concatenate(frames).astype(np.float32) / 32768.0


def chime(freqs=(660, 990)):
    """Short rising two-tone beep."""
    sr = 24000
    parts = []
    for f in freqs:
        t = np.linspace(0, 0.09, int(sr * 0.09), endpoint=False)
        tone = np.sin(2 * np.pi * f * t) * np.hanning(len(t)) * 0.25
        parts.append(tone.astype(np.float32))
    sd.play(np.concatenate(parts), sr)
