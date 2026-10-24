# JARVIS

A voice assistant for Windows that runs **entirely on your own PC**: no cloud APIs, no
accounts, no per-request costs. Say *"Hey Jarvis"* (or click the floating orb) and talk.

## What it can do

- **Spotify:** "play Blinding Lights", "play my workout playlist", "skip", "queue…", "what song is this?"
- **Reminders** that survive restarts: "remind me tomorrow at 6pm to call mom", "every weekday at 9…"
- **Weather and quick answers** from the web: "weather tomorrow?", "who won the game last night?"
- **Screen understanding:** "what's this error?", "summarize this page", "what am I looking at?"
- **Clipboard and files:** "summarize what I copied", "open my latest screenshot", "read that PDF"
- Open and close apps and websites, YouTube, volume, timers, notes, system status, lock the PC
- Long-term memory: "remember that my sister's birthday is March 3rd", "forget my coffee order"
- General conversation, with follow-ups without repeating the wake word
- Interrupt it mid-sentence by saying "Hey Jarvis" again, clicking the orb, or pressing **Ctrl+Alt+J**
- Starts with Windows (optional, from the tray menu) and runs without a console window

## How it works

| Stage | Tool | Runs on |
|---|---|---|
| Wake word | [openWakeWord](https://github.com/dscripka/openWakeWord) ("hey jarvis") | CPU |
| Speech-to-text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `small.en` | GPU (CPU fallback) |
| Brain + tool calling | [Qwen3 8B](https://ollama.com/library/qwen3) via [Ollama](https://ollama.com) | GPU |
| Screen understanding | [Qwen3-VL 4B](https://ollama.com/library/qwen3-vl), loaded only when asked | GPU |
| Voice | [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (`af_bella`) | CPU |
| Overlay | PySide6 (Qt) | — |

Replies stream sentence by sentence, so JARVIS starts speaking about a second after you stop talking.

## Overlay

A glowing orb floats above all windows, with a transcript card next to it.

| Colour | State |
|---|---|
| Blue | Waiting for the wake word |
| Green | Listening (pulses with your voice) |
| Orange | Thinking |
| Cyan | Speaking |
| Red | Wake word paused |

Click the orb to talk, drag it to move it, right-click it (or the tray icon) for options.
It doesn't take focus away from the window you're typing in, and clicks pass through the transcript card.

## Requirements

- Windows 10/11, Python 3.12, a microphone
- An NVIDIA GPU with 8 GB of VRAM is recommended (tested on an RTX 3050 8 GB). It still runs
  without one, but slower.
- About 6 GB of disk space for the models

## Setup

```powershell
git clone https://github.com/aymanokas/jarvis.git
cd jarvis
powershell -ExecutionPolicy Bypass -File setup.ps1
run.bat
```

`setup.ps1` creates the virtual environment, downloads the voice and wake-word models, installs
Ollama if needed and pulls `qwen3:8b`.

Then right-click the orb:
- **Set home city...** for weather when you don't name a place.
- **Connect Spotify...** (needs Premium): create a free app at
  [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) with redirect URI
  `http://127.0.0.1:8888/callback` and "Web API" ticked, then paste its Client ID. No client
  secret is needed. Without Spotify, music requests fall back to YouTube and media keys.

`run.bat` starts JARVIS in the background. `debug.bat` runs it with a console showing the live
log. To launch it at sign-in, right-click the orb and tick **Start with Windows**.

## Privacy and security

- **All AI runs locally.** Audio, transcripts, screenshots and memories never leave your PC.
  Only the lookups you ask for (web answers, weather, YouTube, Spotify) use the internet, and
  only the query is sent. Screenshots are never written to disk.
- **Prompt-injection guard.** Web results, screen contents, clipboard text and files are
  untrusted: they're wrapped as "information, not instructions", and once any of them has been
  read, actions like closing apps, changing memory or opening websites are blocked for the rest
  of that request until you confirm by voice.
- **File access is sandboxed** to Desktop, Downloads, Documents, Pictures, Videos and Music.
  AppData, dot-folders (`.ssh`…), `.env`/key/password files and JARVIS's own data are off
  limits, and only documents and media can be opened, never programs or scripts.
- **Spotify** uses OAuth with PKCE (no client secret), a state check against forged
  callbacks, a loopback-only callback server and minimal scopes. The token is DPAPI-encrypted.
- **Memory is encrypted** with Windows DPAPI: only your Windows account on this PC can read it.
  It's capped at 100 short facts, saved only when you explicitly ask, and passed to the model
  as data rather than instructions. Clear it any time with **Clear memory...** in the tray menu.
- **Personal data stays out of the repo.** Memory, notes and logs live in
  `%LOCALAPPDATA%\Jarvis`, not the project folder. The log rotates at 1 MB.
- **The log contains no conversations.** By default it records events and timings only.
  What JARVIS reads (clipboard, files, screen) is never logged, even with
  `LOG_CONVERSATIONS = True` turned on for debugging.
- **Outside content doesn't linger.** Web, screen, clipboard and file contents are removed from
  the conversation history as soon as the request that read them finishes, so planted
  instructions can't influence a later request.
- **Apps close safely.** "Close Word" closes it like clicking X, so unsaved work triggers the
  usual save prompt. Force-closing only happens if you explicitly ask for it.
- **Idle mode** unloads the models from the GPU after 10 minutes unused (`IDLE_AFTER_MINUTES`).
  The wake word, hotkey and reminders keep working, and models reload while you speak.
- **The hotkey uses `RegisterHotKey`**, so Windows only reports that one key combination.
  JARVIS never sees anything else you type (unlike keyboard-hook libraries).
- **Autostart** is a per-user registry entry (no admin rights) with fully quoted paths, and
  a single-instance lock prevents duplicate copies.
- **Guardrails on actions:** system processes can't be closed, and timers are limited to
  24 hours.
- Keep Ollama bound to `127.0.0.1` (its default). Don't set `OLLAMA_HOST=0.0.0.0`,
  which would expose the model to your network.

## Configuration

Everything lives in [`config.py`](config.py): wake-word sensitivity, silence timing, Whisper
model size, LLM, voice and speaking speed. Other voices: `af_heart`, `bf_emma`, `am_michael`,
`am_fenrir`, `bm_george` (set `VOICE_LANG` to `en-gb` for voices starting with `b`).

## Project layout

```
main.py     assistant loop: wake -> record -> transcribe -> respond
audio.py    microphone, wake word, voice-activity recording
stt.py      Whisper speech-to-text
brain.py    Ollama chat loop with streaming and tool calls
tools.py    actions on the PC (each function is a tool the LLM can call)
tts.py      Kokoro text-to-speech with pipelined synthesis/playback
ui.py       floating overlay and tray icon
memory.py   long-term memory
reminders.py  persistent reminders
web.py      weather and web answers
files.py    clipboard and sandboxed file access
vision.py   screen understanding
spotify.py  Spotify Web API control
securestore.py  DPAPI-encrypted storage and local settings
hotkey.py   global push-to-talk hotkey
autostart.py  start-with-Windows toggle
config.py   settings
```

Adding a tool: write a function with type hints and a docstring in `tools.py` and add it to
`TOOLS`. The LLM picks it up automatically.

## Known limitations

- The overlay can't draw over games in exclusive fullscreen (use borderless windowed).
- Screen questions take about 10 seconds: on an 8 GB GPU the vision model has to swap with the
  main model.
- With speakers instead of a headset, JARVIS can occasionally hear itself during follow-up
  listening. Raise `WAKE_THRESHOLD` or set `FOLLOWUP_TIMEOUT = 0`.
