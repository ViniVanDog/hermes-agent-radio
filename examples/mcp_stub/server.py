#!/usr/bin/env python3
"""A deliberately tiny MCP server, to show how to give the radio agent your own tools.

It exposes two tools:

  radio_time()          the current local time, as one short spoken-style line;
  radio_note(text)      appends a note to a local file and confirms in one line.

Nothing here talks to any external service. Replace the bodies with your own
logic (a home-automation call, a database lookup, ...) and keep the shape:

  * one-line descriptions: Hermes resends every tool schema on every LLM call;
  * short plain-text results, not JSON: results stay in the conversation and
    are often read aloud over the radio;
  * validate arguments and fail with a short sentence the agent can repeat;
  * anyone on the channel can call your tools (docs/security-and-privacy.md):
    keep tools read-only, or limit actions in code, like radio_note's RateLimit;
  * answer in the operator's language: results follow BAOFENG_LANG (en | pt-BR).

Register it in ~/.hermes/config.yaml (use the Python that has the `mcp` package):

    mcp_servers:
      radio_demo:
        command: /path/to/python
        args: ["/home/you/hermes-agent-radio/examples/mcp_stub/server.py"]
        timeout: 30

Hermes then lists the tools as mcp_radio_demo_radio_time and
mcp_radio_demo_radio_note.
"""

from collections import deque
from datetime import datetime
import os
from pathlib import Path
import threading
import time

NOTES = Path(os.environ.get("RADIO_DEMO_NOTES", "~/.local/state/baofeng-radio/notes.txt")).expanduser()
MAX_NOTE_CHARS = 280


class RateLimit:
    """At most `limit` calls per `window_s` seconds, enforced in code, not in the prompt."""

    def __init__(self, limit, window_s, clock=time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self.calls = deque()
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            now = self.clock()
            while self.calls and now - self.calls[0] >= self.window_s:
                self.calls.popleft()
            if len(self.calls) >= self.limit:
                return False
            self.calls.append(now)
            return True


# A write action: 5 notes per 10 minutes, whoever is talking.
NOTE_LIMIT = RateLimit(limit=5, window_s=600)

# Every sentence a tool can return, per language. The agent tends to repeat
# them verbatim, so write them the way they should sound.
MESSAGES = {
    "en": {
        "time": "It is {time} on {weekday}, {day} {month}.",
        "empty": "Nothing to save: the note was empty.",
        "long": "Note too long: keep it under {limit} characters.",
        "rate": "Too many notes in the last few minutes; try again later.",
        "saved": "Saved note: {text}",
        "weekdays": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        "months": ("January", "February", "March", "April", "May", "June", "July",
                   "August", "September", "October", "November", "December"),
    },
    "pt": {
        "time": "São {time} de {weekday}, {day} de {month}.",
        "empty": "Nada para salvar: a nota estava vazia.",
        "long": "Nota longa demais: use menos de {limit} caracteres.",
        "rate": "Notas demais nos últimos minutos; tente de novo mais tarde.",
        "saved": "Nota salva: {text}",
        "weekdays": ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
                     "sexta-feira", "sábado", "domingo"),
        "months": ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                   "agosto", "setembro", "outubro", "novembro", "dezembro"),
    },
}


def language():
    return "pt" if os.environ.get("BAOFENG_LANG", "en").strip().lower().startswith("pt") else "en"


def radio_time(now=None, lang=None):
    """Current local time in one short line."""
    now = now or datetime.now().astimezone()
    say = MESSAGES[lang or language()]
    return say["time"].format(time=f"{now:%H:%M}", weekday=say["weekdays"][now.weekday()],
                              day=now.day, month=say["months"][now.month - 1])


def radio_note(text, path=NOTES, now=None, limit=NOTE_LIMIT, lang=None):
    """Save a short note; returns a one-line confirmation."""
    say = MESSAGES[lang or language()]
    text = " ".join(str(text or "").split())
    if not text:
        return say["empty"]
    if len(text) > MAX_NOTE_CHARS:
        return say["long"].format(limit=MAX_NOTE_CHARS)
    if not limit.allow():
        return say["rate"]
    now = now or datetime.now().astimezone()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as notes:
        notes.write(f"{now.isoformat(timespec='seconds')}\t{text}\n")
    return say["saved"].format(text=text)


def main():
    from mcp.server.fastmcp import FastMCP  # pip install mcp

    server = FastMCP("radio_demo")
    server.tool(description="Current local time.")(radio_time_tool)
    server.tool(description="Save a short note the user dictates.")(radio_note_tool)
    server.run()


def radio_time_tool() -> str:
    return radio_time()


def radio_note_tool(text: str) -> str:
    return radio_note(text)


if __name__ == "__main__":
    main()
