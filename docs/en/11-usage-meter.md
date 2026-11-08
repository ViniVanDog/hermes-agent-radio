[Português (Brasil)](../11-usage-meter.md) · **English**

# 11. Usage meter

You cannot trim what you do not measure. `radio_bridge/usage.py` (stdlib
only) builds a per-day table from two sources:

- **Hermes' own accounting**: `~/.hermes/state.db`, table
  `session_model_usage`, opened **read-only**. It gives LLM calls and tokens
  (uncached input, cached input, output, reasoning) per Hermes task
  (conversation, background_review, title_generation, …).
- **The radio event log**, `~/.local/state/baofeng-radio/events.jsonl`:
  `stt` (one per transcribed transmission), `tts_audio_closed` (replies and
  their audio minutes), `announce` (spoken without the LLM) and
  `context_reset`.

```bash
python3 -m radio_bridge.usage 7          # last 7 days
BAOFENG_TZ=Europe/Lisbon python3 -m radio_bridge.usage 30
```

Example output (illustrative numbers):

```text
day         LLM   uncached  cache  output  tok/call  STT  TTS(min)  announce  resets
2026-11-06    41       38k   212k      9k        6k   44   39(6)         12       3
            (conversation 39, title_generation 2)
```

The dashboard shows the same table in its **API usage per day** card
(`/api/usage`). It loads the module from the checkout, or from
`~/.local/share/baofeng-radio/` where `radio_bridge/install.py` puts it.

Caveat: Hermes stores totals **per session and task**, not per call, so a
session's usage counts on the day of its last call. A session that spans
midnight lands entirely on the second day.

Variables: `HERMES_STATE_DB`, `BAOFENG_EVENTS_FILE`, `BAOFENG_TZ` (default:
the system zone).
