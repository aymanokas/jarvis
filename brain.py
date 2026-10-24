"""The LLM loop: streams the reply sentence by sentence and runs tool calls."""
import datetime
import logging
import re
import time

import ollama

import config
import tools

log = logging.getLogger("jarvis.brain")

SYSTEM_PROMPT = """You are JARVIS, a warm, quick-witted AI assistant running locally on the \
user's Windows PC. You talk like a friendly, capable person, not a machine: relaxed, natural, \
with a light touch of humour when it fits.

Your replies are spoken aloud, so write the way people actually talk:
- Use contractions (I've, you're, that's) and everyday words. Never sound formal or scripted.
- Keep it short: usually one or two sentences. Go longer only if asked.
- Vary your phrasing. Don't start every reply the same way or repeat the user's words back.
- Plain sentences only: no markdown, lists, emojis or URLs.
- Write numbers, times and units the way they're said aloud ("ten minutes", "about half past three").
- Don't end with filler like "anything else?", "what's next?" or "let me know if you need anything".

When a request involves the computer, use your tools. Never claim you did something \
without calling the tool. Never offer to do things you have no tool for. Afterwards confirm casually, like "Done, it's open" or "Ten minutes, \
starting now." If something's unclear, just ask.

Memory: when the user explicitly asks you to remember something, call remember with a short \
standalone fact. Don't save things on your own initiative. Whenever the user asks you to \
forget, delete or stop remembering something, you MUST call the forget tool; saying you've \
forgotten without calling it is a lie, because the memory stays saved. Use what you remember \
naturally, without announcing it.

{memory}

Current date and time: {now}."""

SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
MIN_CHUNK = 40  # characters; shorter sentences wait to be spoken with the next one


class Brain:
    def __init__(self):
        self.client = ollama.Client()
        self.history = []

    def warm_up(self, wait=120):
        """Loads the model into VRAM and keeps it there. At sign-in Ollama may still be
        starting, so keep retrying for up to `wait` seconds."""
        deadline = time.time() + wait
        while True:
            try:
                self.client.chat(model=config.OLLAMA_MODEL, messages=[{"role": "user", "content": "hi"}],
                                 think=False, keep_alive=-1,
                                 options={"num_ctx": config.NUM_CTX, "num_predict": 1})
                return
            except ConnectionError:
                if time.time() > deadline:
                    raise
                log.info("Waiting for Ollama to start...")
                time.sleep(3)

    def _system(self):
        now = datetime.datetime.now().strftime("%A, %B %d %Y, %I:%M %p")
        return {"role": "system", "content": SYSTEM_PROMPT.format(now=now, memory=tools.memory.prompt_block())}

    def _trim(self):
        if len(self.history) <= config.MAX_HISTORY:
            return
        cut = len(self.history) - config.MAX_HISTORY
        # Start on a user message so tool results never lose their call.
        while cut < len(self.history) and self.history[cut]["role"] != "user":
            cut += 1
        self.history = self.history[cut:]

    def ask(self, text, on_sentence, cancel):
        """Streams a reply to on_sentence(str). Stops early if cancel is set."""
        self.history.append({"role": "user", "content": text})
        for _ in range(5):  # max tool rounds per request
            content, buffer, pending, calls = "", "", "", []
            stream = self.client.chat(
                model=config.OLLAMA_MODEL, messages=[self._system()] + self.history,
                tools=tools.TOOLS, stream=True, think=False, keep_alive=-1,
                options={"num_ctx": config.NUM_CTX, "temperature": config.TEMPERATURE},
            )
            for part in stream:
                if cancel.is_set():
                    break
                msg = part.message
                if msg.content:
                    content += msg.content
                    buffer += msg.content
                    *done, buffer = SENTENCE_END.split(buffer)
                    for sentence in done:
                        # Group short sentences: the voice sounds more natural with context.
                        pending = f"{pending} {sentence}".strip()
                        if len(pending) >= MIN_CHUNK:
                            on_sentence(pending)
                            pending = ""
                if msg.tool_calls:
                    calls.extend(msg.tool_calls)
            rest = f"{pending} {buffer}".strip()
            if rest and not cancel.is_set():
                on_sentence(rest)

            self.history.append({"role": "assistant", "content": content,
                                 "tool_calls": [c.model_dump() for c in calls] or None})
            if content.strip():
                log.info("jarvis: %s", content.strip())
            if cancel.is_set() or not calls:
                break
            for call in calls:
                result = tools.run(call.function.name, call.function.arguments)
                self.history.append({"role": "tool", "content": str(result),
                                     "tool_name": call.function.name})
        self._trim()

    def reset(self):
        self.history = []
