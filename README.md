# JARVIS

A voice assistant for Windows that runs **entirely on your own PC**: no cloud APIs, no
accounts, no per-request costs. Say *"Hey Jarvis"* (or click the floating orb) and talk.

## What it can do

- Open and close apps, open websites, search the web, play things on YouTube
- Play/pause/skip media, set or adjust volume, mute
- Timers that announce themselves, quick notes, system status (CPU, RAM, GPU)
- Lock the PC
- General conversation, with follow-ups without repeating the wake word
- Interrupt it mid-sentence by saying "Hey Jarvis" again or clicking the orb

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
config.py   settings
```

Adding a tool: write a function with type hints and a docstring in `tools.py` and add it to
`TOOLS`. The LLM picks it up automatically.

## Known limitations

- The overlay can't draw over games in exclusive fullscreen (use borderless windowed).
- With speakers instead of a headset, JARVIS can occasionally hear itself during follow-up
  listening. Raise `WAKE_THRESHOLD` or set `FOLLOWUP_TIMEOUT = 0`.
