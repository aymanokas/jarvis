"""Speech-to-text with faster-whisper (GPU if available, else CPU)."""
import logging
import os
import sys

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
from faster_whisper import WhisperModel  # noqa: E402

# Phrases Whisper tends to invent from background noise.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "bye", "thank you for watching"}


class STT:
    def __init__(self):
        try:
            self.model = WhisperModel(config.WHISPER_MODEL, device="cuda", compute_type="float16")
            list(self.model.transcribe(np.zeros(config.SAMPLE_RATE, np.float32))[0])  # forces cuDNN load
            self.device = "cuda"
        except Exception as e:
            log.warning("GPU whisper unavailable (%s), using CPU", e)
            self.model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=8)
            self.device = "cpu"
        log.info("Whisper %s loaded on %s", config.WHISPER_MODEL, self.device)

    def transcribe(self, audio):
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=3, vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        if text.lower().strip(" .!?,") in HALLUCINATIONS:
            return ""
        return text
