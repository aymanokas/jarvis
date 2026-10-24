"""Reminders that survive restarts (encrypted at rest), one-off or recurring."""
import datetime
import logging
import threading
import time

import config
import securestore
from memory import clean, find

log = logging.getLogger("jarvis.reminders")

FORMAT = "%Y-%m-%d %H:%M"
REPEATS = ("none", "daily", "weekdays", "weekly")
MAX_REMINDERS = 50


def _next(due, repeat):
    if repeat == "daily":
        return due + datetime.timedelta(days=1)
    if repeat == "weekly":
        return due + datetime.timedelta(weeks=1)
    if repeat == "weekdays":
        due += datetime.timedelta(days=1)
        while due.weekday() >= 5:
            due += datetime.timedelta(days=1)
        return due
    return None


def spoken_time(due):
    now = datetime.datetime.now()
    clock = due.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
    if due.date() == now.date():
        return f"today at {clock}"
    if due.date() == now.date() + datetime.timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"{due.strftime('%A %B')} {due.day} at {clock}"


class Reminders:
    def __init__(self, path, on_fire):
        self.path = path
        self.on_fire = on_fire
        self._lock = threading.Lock()
        self.items = securestore.load(path, [])  # [{"text", "due": FORMAT, "repeat"}]
        self._catch_up()
        threading.Thread(target=self._loop, daemon=True, name="reminders").start()

    def _save(self):
        securestore.save(self.path, self.items)

    def _catch_up(self):
        """Handle reminders that came due while JARVIS wasn't running."""
        now = datetime.datetime.now()
        missed, keep = [], []
        for r in self.items:
            due = datetime.datetime.strptime(r["due"], FORMAT)
            if due > now:
                keep.append(r)
                continue
            if now - due <= datetime.timedelta(hours=config.MISSED_REMINDER_HOURS):
                missed.append(r["text"])
            if r["repeat"] != "none":
                while due <= now:
                    due = _next(due, r["repeat"])
                keep.append({**r, "due": due.strftime(FORMAT)})
        self.items = keep
        self._save()
        if missed:
            threading.Timer(15, lambda: self.on_fire(
                "While I was offline, you missed: " + "; ".join(missed) + ".")).start()

    def _loop(self):
        while True:
            time.sleep(5)
            now = datetime.datetime.now()
            fired = []
            with self._lock:
                for r in list(self.items):
                    due = datetime.datetime.strptime(r["due"], FORMAT)
                    if due > now:
                        continue
                    fired.append(r["text"])
                    nxt = _next(due, r["repeat"])
                    if nxt:
                        while nxt <= now:
                            nxt = _next(nxt, r["repeat"])
                        r["due"] = nxt.strftime(FORMAT)
                    else:
                        self.items.remove(r)
                if fired:
                    self._save()
            for text in fired:
                log.info("reminder fired")
                self.on_fire(f"Reminder: {text}")

    def add(self, text, when, repeat="none"):
        text = clean(text, 200)
        repeat = (repeat or "none").lower()
        if repeat not in REPEATS:
            return f"Repeat must be one of: {', '.join(REPEATS)}."
        try:
            due = datetime.datetime.strptime(when.strip(), FORMAT)
        except ValueError:
            return "Time must be in the format YYYY-MM-DD HH:MM (24-hour)."
        if due <= datetime.datetime.now():
            return "That time has already passed. Ask the user for a future time."
        if due > datetime.datetime.now() + datetime.timedelta(days=366):
            return "Reminders can be at most a year ahead."
        with self._lock:
            if len(self.items) >= MAX_REMINDERS:
                return "Too many reminders. Ask the user to cancel some first."
            self.items.append({"text": text, "due": due.strftime(FORMAT), "repeat": repeat})
            self._save()
        every = "" if repeat == "none" else f", repeating {repeat}"
        return f"Reminder set for {spoken_time(due)}{every}: {text}"

    def list(self):
        if not self.items:
            return "No reminders set."
        items = sorted(self.items, key=lambda r: r["due"])
        return "\n".join(
            f"- {r['text']}: {spoken_time(datetime.datetime.strptime(r['due'], FORMAT))}"
            + ("" if r["repeat"] == "none" else f" (repeats {r['repeat']})") for r in items)

    def cancel(self, query):
        with self._lock:
            i = find(self.items, clean(query))
            if i is None:
                return "No matching reminder."
            removed = self.items.pop(i)
            self._save()
        return f"Cancelled reminder: {removed['text']}"
