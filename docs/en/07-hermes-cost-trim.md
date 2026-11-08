[Português (Brasil)](../07-hermes-cost-trim.md) · **English**

# 7. Keeping Hermes cheap on the radio

Every Hermes LLM call carries the system prompt, **every tool schema**, and
the **whole conversation**. A radio agent runs all day and answers short
questions, so these fixed costs dominate. The changes below come from
measuring our own deployment with the [usage meter](11-usage-meter.md).

## 1. Trim the config (`apply_radio_config.py`)

Run it with **Hermes' own Python** (it needs `ruamel.yaml`, which keeps your
comments):

```bash
<hermes-venv>/bin/python hermes_patches/apply_radio_config.py --dry-run     # show changes
<hermes-venv>/bin/python hermes_patches/apply_radio_config.py [--keep skill-a skill-b]
systemctl --user restart hermes-radio
```

It backs up `config.yaml` first, restores the backup if the result does
not parse, and is idempotent.

| Setting | Effect we measured |
|---|---|
| `auxiliary.background_review.enabled: false` | Turns off Hermes' post-turn self-review fork. It was **113 of our first 590 calls and 28% of input tokens**. |
| `auxiliary.title_generation.model_upgrade_enabled: false` | Keeps instant local session titles, with no extra model call per session. |
| `platform_toolsets.cli: [skills]` | Drops the file, terminal and vision tool schemas: **~3k tokens per call**, plus the "approval" calls they trigger. MCP tools you register are unaffected. |
| `skills.disabled: <everything but --keep>` | The `<available_skills>` list was **~6k characters per call** of skills nobody uses by radio. |

> **Security bonus.** Without the terminal and file toolsets, nobody on the
> radio channel can talk the agent into running commands on its machine.
> Anyone with a radio and your squelch tone can reach it, so treat the
> channel as untrusted input ([security and privacy](security-and-privacy.md)).

## 2. Don't send audio that isn't speech

- The radio's **CTCSS/DCS tone** keeps most unrelated traffic from opening the squelch.
  It is a noise filter, not access control ([why](security-and-privacy.md)).
- The **RX gate** ignores bursts under 0.8 s of real audio (key-clicks,
  squelch tails). They never reach STT.
- The **input filter** drops the radio's own voice prompts after STT, before
  the LLM.

## 3. Keep the conversation short

Hermes does not compress the history of an interactive session, so the
input of every call grows through the day. Two bounds:

- **Idle reset** (`HERMES_RADIO_IDLE_RESET_MIN`, default 20): after 20
  quiet minutes the next transmission starts a fresh session. The
  dashboard's *Clear context* button does the same on demand.
- **Token rollover** (`HERMES_RADIO_CONTEXT_MAX_TOKENS`, default 20,000):
  between turns, once the last prompt passed the limit, a new session starts
  carrying only the last request, the final reply verbatim (so a following
  "yes" still makes sense), and the newest tool results that fit 8,000
  characters. **No summarization model call.** It never interrupts a running
  turn. The old session stays in Hermes' history.

## 4. Speak without thinking

Alerts, reminders, time checks and the like don't need a model. Write them
to the announcement spool and Hermes reads them aloud through TTS at the
next idle moment, for **zero tokens** ([chapter 12](12-adding-your-own-tools.md)).
Only the operator's reply, if any, becomes an LLM turn.

## 5. Small tools, small results

If you add tools ([chapter 12](12-adding-your-own-tools.md)):

- one-line descriptions, because schemas are resent on every call;
- return a few lines of plain text, not JSON, because results stay in the history;
- cap list results (e.g. 8 items), and offer a search argument instead of
  "list everything".

## 6. Measure

```bash
python3 -m radio_bridge.usage 7
```

This shows calls, uncached/cached input, output and tokens per call per day,
split by Hermes task (conversation, background_review, title_generation, …).
Check it before and after each change.
