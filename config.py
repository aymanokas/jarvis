"""All tunable settings in one place."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(ROOT, "models")

# --- Audio ---
SAMPLE_RATE = 16000
CHUNK = 1280                # 80 ms, the frame size openWakeWord expects
INPUT_DEVICE = None         # None = Windows default mic; or an index from `python -m sounddevice`

# --- Wake word ---
WAKE_THRESHOLD = 0.5        # raise if it triggers by itself, lower if it misses you

# --- Recording (after wake word) ---
VAD_AGGRESSIVENESS = 2      # 0-3, higher = stricter about what counts as speech
START_TIMEOUT = 5.0         # seconds to wait for you to start talking
FOLLOWUP_TIMEOUT = 5.0      # after a reply, keep listening this long without the wake word
END_SILENCE = 0.8           # seconds of silence that ends your sentence
MAX_RECORD = 20.0

# --- Speech-to-text ---
WHISPER_MODEL = "small.en"  # tiny.en / base.en / small.en / medium.en / distil-large-v3

# --- LLM ---
OLLAMA_MODEL = "qwen3:8b"
NUM_CTX = 4096              # keeps the model fully on an 8 GB GPU
TEMPERATURE = 0.6
MAX_HISTORY = 20            # messages kept for conversation context

# --- Text-to-speech ---
KOKORO_MODEL = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.join(MODELS_DIR, "voices-v1.0.bin")
VOICE = "af_bella"          # Others: af_heart, bf_emma (en-gb), am_michael, am_fenrir, bm_george (en-gb)
VOICE_LANG = "en-us"        # must match the voice: en-us for a*_ voices, en-gb for b*_ voices
VOICE_SPEED = 1.0

# --- Hotkey (works anywhere, same as clicking the orb) ---
HOTKEY = "ctrl+alt+j"       # modifiers: ctrl, alt, shift, win; key: a-z, 0-9, f1-f12, space

# --- Memory ---
MAX_MEMORIES = 100
MAX_MEMORY_CHARS = 300

# Personal data lives outside the repo so it can never be committed by accident.
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Jarvis")
os.makedirs(DATA_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(DATA_DIR, "memory.bin")   # encrypted with Windows DPAPI
NOTES_FILE = os.path.join(DATA_DIR, "notes.txt")
LOG_FILE = os.path.join(DATA_DIR, "jarvis.log")
