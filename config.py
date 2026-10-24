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
NUM_CTX = 8192              # fits on an 8 GB GPU with OLLAMA_KV_CACHE_TYPE=q8_0 (set by setup.ps1)
TEMPERATURE = 0.6
MAX_HISTORY = 20            # messages kept for conversation context

# --- Text-to-speech ---
KOKORO_MODEL = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.join(MODELS_DIR, "voices-v1.0.bin")
VOICE = "af_bella"          # Others: af_heart, bf_emma (en-gb), am_michael, am_fenrir, bm_george (en-gb)
VOICE_LANG = "en-us"        # must match the voice: en-us for a*_ voices, en-gb for b*_ voices
VOICE_SPEED = 1.0

# --- Idle mode ---
IDLE_AFTER_MINUTES = 10     # unload models from the GPU after this long unused; 0 = never

# --- Hotkey (works anywhere, same as clicking the orb) ---
HOTKEY = "ctrl+alt+j"       # modifiers: ctrl, alt, shift, win; key: a-z, 0-9, f1-f12, space

# --- Memory ---
MAX_MEMORIES = 100
MAX_MEMORY_CHARS = 300

# Personal data lives outside the repo so it can never be committed by accident.
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Jarvis")
os.makedirs(DATA_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(DATA_DIR, "memory.bin")       # encrypted with Windows DPAPI
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.bin")  # encrypted
SPOTIFY_TOKEN_FILE = os.path.join(DATA_DIR, "spotify.bin")  # encrypted
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")  # home city, Spotify client ID
NOTES_FILE = os.path.join(DATA_DIR, "notes.txt")
LOG_FILE = os.path.join(DATA_DIR, "jarvis.log")
# The log is plain text. Off: it records events and timings only, never what was said,
# read or written. Turn on temporarily when debugging a misheard or misrouted request.
LOG_CONVERSATIONS = False

# --- Screen understanding ---
VISION_MODEL = "qwen3-vl:4b"   # loaded only when asked to look, then unloaded
VISION_MAX_SIDE = 1280         # screenshots are downscaled to this before analysis

# --- Untrusted content limits (web, clipboard, files) ---
MAX_READ_CHARS = 6000          # keeps documents inside the model's context window
MISSED_REMINDER_HOURS = 12     # reminders missed while the PC was off are still announced
