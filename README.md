# JARVIS

A voice assistant for Windows that runs **entirely on your own PC**: no cloud APIs, no
accounts, no per-request costs. Say *"Hey Jarvis"* (or click the floating orb) and talk.

## What it can do

- Open and close apps, open websites, search the web, play things on YouTube
- Play/pause/skip media, set or adjust volume, mute
- Timers that announce themselves, quick notes, system status (CPU, RAM, GPU)
- Lock the PC
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

`run.bat` starts JARVIS in the background. `debug.bat` runs it with a console showing the live
log. To launch it at sign-in, right-click the orb and tick **Start with Windows**.

## Privacy and security

- **All AI runs locally.** Audio, transcripts and memories never leave your PC. Only the
  web tools you ask for (search, YouTube, websites) use the internet.
- **Memory is encrypted** with Windows DPAPI: only your Windows account on this PC can read it.
  It's capped at 100 short facts, saved only when you explicitly ask, and passed to the model
  as data rather than instructions. Clear it any time with **Clear memory...** in the tray menu.
- **Personal data stays out of the repo.** Memory, notes and logs live in
  `%LOCALAPPDATA%\Jarvis`, not the project folder. The log rotates at 1 MB.
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
memory.py   encrypted long-term memory
hotkey.py   global push-to-talk hotkey
autostart.py  start-with-Windows toggle
config.py   settings
```

Adding a tool: write a function with type hints and a docstring in `tools.py` and add it to
`TOOLS`. The LLM picks it up automatically.

## Known limitations

- The overlay can't draw over games in exclusive fullscreen (use borderless windowed).
- With speakers instead of a headset, JARVIS can occasionally hear itself during follow-up
  listening. Raise `WAKE_THRESHOLD` or set `FOLLOWUP_TIMEOUT = 0`.
