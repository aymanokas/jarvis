"""Speech-to-text with faster-whisper (GPU if available, else CPU)."""
import gc
import logging
import os
import sys
import threading

import numpy as np

import config

log = logging.getLogger("jarvis.stt")


def _add_cuda_dlls():
    """Make the pip-installed cuBLAS/cuDNN DLLs visible to CTranslate2."""
    base = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        path = os.path.join(base, sub, "bin")
        if os.path.isdir(path):
            os.add_dll_directory(path)
            os.environ["PATH"] = path + os.pathsep + os.environ["PATH"]


_add_cuda_dlls()
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
from faster_whisper import WhisperModel  # noqa: E402

# Phrases Whisper tends to invent from background noise.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "bye", "thank you for watching"}


def _load(**kwargs):
    """Use the cached model without contacting the internet; download only the first time."""
    try:
        return WhisperModel(config.WHISPER_MODEL, local_files_only=True, **kwargs)
    except Exception:
        log.info("Whisper %s not cached yet, downloading", config.WHISPER_MODEL)
        return WhisperModel(config.WHISPER_MODEL, **kwargs)


class STT:
    def __init__(self):
        self.model = None
        self._lock = threading.Lock()
        self.load()

    def load(self):
        with self._lock:
            if self.model is not None:
                return
            try:
                self.model = _load(device="cuda", compute_type="float16")
                list(self.model.transcribe(np.zeros(config.SAMPLE_RATE, np.float32))[0])  # forces cuDNN load
                self.device = "cuda"
            except Exception as e:
                log.warning("GPU whisper unavailable (%s), using CPU", e)
                self.model = _load(device="cpu", compute_type="int8", cpu_threads=8)
                self.device = "cpu"
            log.info("Whisper %s loaded on %s", config.WHISPER_MODEL, self.device)

    def unload(self):
        """Frees GPU memory while JARVIS sleeps."""
        with self._lock:
            self.model = None
        gc.collect()

    def transcribe(self, audio):
        self.load()  # no-op unless asleep; waits if a wake-up reload is in progress
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=3, vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        if text.lower().strip(" .!?,") in HALLUCINATIONS:
            return ""
        return text
