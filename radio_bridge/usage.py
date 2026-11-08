#!/usr/bin/env python3
"""Daily API usage of the Hermes radio: LLM calls/tokens, STT, TTS, alerts.

Reads Hermes' own accounting (~/.hermes/state.db, table session_model_usage, opened
read-only) and the radio event log (~/.local/state/baofeng-radio/events.jsonl).
Stdlib only, so the dashboard can import it with the system Python.

  LLM: every call Hermes makes, by task (conversation, background_review,
             title_generation, approval); input split into uncached and cached.
  STT: one call per transmission transcribed ("stt" events).
  TTS: replies and their audio minutes ("tts_audio_closed").
  Alerts: "announce" (spoken without LLM) and "context_reset".

A session's usage is counted on the day of its last call (Hermes stores totals per
session and task, not per call).

Usage: python3 -m radio_bridge.usage [days]
Time zone: BAOFENG_TZ (e.g. "Europe/Lisbon"), else the system local zone.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATE_DB = os.path.expanduser(os.environ.get("HERMES_STATE_DB", "~/.hermes/state.db"))
EVENTS = os.path.expanduser(os.environ.get("BAOFENG_EVENTS_FILE", "~/.local/state/baofeng-radio/events.jsonl"))
TZ = ZoneInfo(os.environ["BAOFENG_TZ"]) if os.environ.get("BAOFENG_TZ") else None  # None = local
EVENT_KINDS = ("stt", "announce", "context_reset")


def _day(epoch):
    return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def _empty(day):
    return {"day": day, "llm_calls": 0, "input": 0, "cached": 0, "output": 0, "reasoning": 0,
            "by_task": {}, "stt": 0, "tts": 0, "tts_minutes": 0.0, "announce": 0,
            "context_reset": 0}


def _llm_rows(db_path):
    if not os.path.exists(db_path):
        return []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as db:
        return db.execute(
            "select coalesce(nullif(task, ''), 'conversation'), last_seen, api_call_count, input_tokens,"
            " cache_read_tokens, output_tokens, reasoning_tokens from session_model_usage").fetchall()


def _events(path):
    try:
        with open(path, encoding="utf-8") as events:
            for line in events:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def summary(days=7, db_path=STATE_DB, events_path=EVENTS, now=None):
    """Newest day first: [{day, llm_calls, input, cached, output, by_task, stt, tts, ...}]."""
    now = now or datetime.now(timezone.utc)
    first = (now.astimezone(TZ) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    table = {}

    def row(day):
        return table.setdefault(day, _empty(day))

    for task, last_seen, calls, uncached, cached, output, reasoning in _llm_rows(db_path):
        if not last_seen:
            continue
        day = _day(last_seen)
        if day < first:
            continue
        r = row(day)
        r["llm_calls"] += calls or 0
        r["input"] += uncached or 0
        r["cached"] += cached or 0
        r["output"] += output or 0
        r["reasoning"] += reasoning or 0
        r["by_task"][task] = r["by_task"].get(task, 0) + (calls or 0)
    for event in _events(events_path):
        kind = event.get("kind")
        if kind not in EVENT_KINDS and kind != "tts_audio_closed" or "ts" not in event:
            continue
        day = _day(event["ts"])
        if day < first:
            continue
        r = row(day)
        if kind == "tts_audio_closed":
            r["tts"] += 1
            r["tts_minutes"] = round(r["tts_minutes"] + (event.get("duration_ms") or 0) / 60000, 1)
        else:
            r[kind] += 1
    for r in table.values():
        r["tokens_per_call"] = round((r["input"] + r["cached"]) / r["llm_calls"]) if r["llm_calls"] else 0
    return sorted(table.values(), key=lambda r: r["day"], reverse=True)


def _k(value):
    return f"{value / 1000:.0f}k" if value >= 1000 else str(value)


def text(rows):
    lines = ["day         LLM   uncached  cache  output  tok/call  STT  TTS(min)  announce  resets"]
    for r in rows:
        lines.append(f"{r['day']}  {r['llm_calls']:4d}  {_k(r['input']):>9}  {_k(r['cached']):>5}  {_k(r['output']):>5}"
                     f"  {_k(r['tokens_per_call']):>9}  {r['stt']:3d}  {r['tts']:3d}({r['tts_minutes']:.0f})"
                     f"  {r['announce']:8d}  {r['context_reset']:6d}")
        tasks = ", ".join(f"{task} {n}" for task, n in sorted(r["by_task"].items(), key=lambda kv: -kv[1]))
        if tasks:
            lines.append(f"            ({tasks})")
    return "\n".join(lines)


if __name__ == "__main__":
    print(text(summary(int(sys.argv[1]) if len(sys.argv) > 1 else 7)))
