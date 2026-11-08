#!/usr/bin/env python3
"""Minimal announcement producer: Hermes speaks these over the radio without an LLM call.

apply_radio_announce.py makes Hermes watch a spool directory. Any program can
drop a JSON file there; when the radio is idle, Hermes reads the text aloud
through its TTS and deletes the file. This example announces the time on the
hour, between --start and --end. Replace `due_announcements()` with your own
rules (a sensor threshold, a calendar entry, a finished job...).
The example's text follows BAOFENG_LANG (en | pt-BR).

    python3 examples/announcer_stub/announcer.py --say "Test announcement."
    python3 examples/announcer_stub/announcer.py --loop

File format (one announcement per file, oldest name first):

    {"kind": "announce", "text": "...", "at": "<ISO 8601 time with zone>"}

Files older than HERMES_RADIO_ANNOUNCE_MAX_AGE_S (default 10 min) are dropped
unspoken, so a stopped Hermes never replays a backlog.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

SPOOL = Path(os.environ.get("HERMES_RADIO_ANNOUNCE_DIR", "~/.local/state/baofeng-radio/announce")).expanduser()


def write_announcement(text, spool=SPOOL, now=None):
    """Atomically queue one announcement; returns its path."""
    now = now or datetime.now(timezone.utc)
    spool = Path(spool)
    spool.mkdir(parents=True, exist_ok=True)
    name = f"{now.strftime('%Y%m%dT%H%M%S%f')}-{os.getpid()}.json"
    temporary = spool / ("." + name)        # dot-files are ignored by the reader
    temporary.write_text(json.dumps({"kind": "announce", "text": text, "at": now.isoformat()},
                                    ensure_ascii=False), encoding="utf-8")
    final = spool / name
    temporary.replace(final)
    return final


def language():
    return "pt" if os.environ.get("BAOFENG_LANG", "en").strip().lower().startswith("pt") else "en"


def time_check(hour, lang=None):
    """The hour as one spoken sentence."""
    if (lang or language()) == "pt":
        special = {0: "É meia-noite.", 1: "É uma hora.", 12: "É meio-dia."}
        return special.get(hour, f"São {hour} horas.")
    return f"It is {hour} o'clock."


def due_announcements(now, last_hour, start, end, lang=None):
    """The example rule: one time check per hour inside [start, end)."""
    if now.minute == 0 and now.hour != last_hour and start <= now.hour < end:
        return [time_check(now.hour, lang)]
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--say", help="queue this text once and exit")
    parser.add_argument("--loop", action="store_true", help="run the hourly example rule")
    parser.add_argument("--start", type=int, default=9, help="first hour to announce (default 9)")
    parser.add_argument("--end", type=int, default=18, help="stop announcing at this hour (default 18)")
    args = parser.parse_args()
    if args.say:
        print(write_announcement(args.say))
        return
    if not args.loop:
        parser.error("use --say TEXT or --loop")
    last_hour = None
    while True:
        now = datetime.now().astimezone()
        for text in due_announcements(now, last_hour, args.start, args.end):
            print(write_announcement(text), flush=True)
            last_hour = now.hour
        time.sleep(20)


if __name__ == "__main__":
    main()
