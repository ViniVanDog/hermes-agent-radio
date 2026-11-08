[Português (Brasil)](../12-adding-your-own-tools.md) · **English**

# 12. Adding your own tools

This repo stops at "an agent you can talk to by radio". What it can *do*
is up to you. There are two patterns, each with a runnable stub in `examples/`.

> **Anyone on the channel can call your tools.** Start read-only. Any tool
> that changes something needs hard limits and a rate limit **in code**,
> plus an audit log. Never let a tool return personal data, credentials or
> codes. Read [security and privacy](security-and-privacy.md) before you add
> anything.

## A. Tools the agent calls: MCP

Hermes loads tools from MCP servers listed in `~/.hermes/config.yaml`.
`examples/mcp_stub/server.py` is a deliberately tiny one with two tools,
`radio_time` and `radio_note`. Neither talks to any outside service.

```bash
<hermes-venv>/bin/pip install mcp            # if Hermes' venv lacks it
```

```yaml
mcp_servers:
  radio_demo:
    command: /path/to/hermes-venv/bin/python
    args: ["/home/you/hermes-agent-radio/examples/mcp_stub/server.py"]
    timeout: 30
```

Restart `hermes-radio`. The tools appear as `mcp_radio_demo_radio_time`
and `mcp_radio_demo_radio_note`. Ask "what time is it?" over the radio.

Design rules that matter more on a radio than anywhere else:

| Rule | Why |
|---|---|
| **One-line tool descriptions** | Every schema is resent on every LLM call ([chapter 7](07-hermes-cost-trim.md)). |
| **Plain text results, a few lines** | Results stay in the conversation, and the reply built from them is spoken. JSON gets read out as punctuation soup. |
| **Cap lists (≈8 items) and offer a search argument** | "List everything" blows up the context and the listener's patience. |
| **Accept fuzzy input** | STT misspells names. Match tolerantly, and when several match, return "Which one: A or B?" rather than guessing. |
| **Batch arguments** | `do_thing(items=[…])` beats N calls: fewer round-trips, less dead air. |
| **Validate and fail in one sentence** | The agent will repeat your error message to the operator verbatim. |
| **Rate-limit every action in code** | `radio_note` uses `RateLimit(5, 600)`: five notes per ten minutes, whoever is asking. Validation failures don't use up the budget. |
| **Ask before irreversible actions; don't ask before harmless ones** | A spoken "yes" guards against **mishearing**, not impostors: anyone can say it. Keep irreversible or high-value actions off the radio entirely. |
| **Return the minimum** | Counts, statuses, initials. Filter sensitive fields in the tool, not in the prompt. |
| **Set a timeout that matches the slowest real call** | `timeout:` in `mcp_servers`. On a timeout, check whether the action happened before retrying. |

Both stubs answer in `BAOFENG_LANG` (`en` or `pt-BR`). Keep every sentence a
tool can return in one table per language, like `MESSAGES` in the MCP
stub: the agent repeats them close to verbatim, so they should already
sound right in the operator's language.

`examples/mcp_stub/test_server.py` shows how to unit-test the tool bodies
without Hermes.

## B. Things the agent says unprompted: announcements

For alerts and reminders, **don't** wake the LLM. Drop a JSON file in the
spool, and the [announce patch](06-hermes-integration.md) speaks it at the
next idle moment through TTS alone, for zero tokens:

```bash
python3 examples/announcer_stub/announcer.py --say "Test announcement."
python3 examples/announcer_stub/announcer.py --loop        # example: time check on the hour, 09–18
systemctl --user enable --now baofeng-announcer             # same, as a service
```

```json
{"kind": "announce", "text": "Generator fuel below twenty percent.", "at": "2026-11-06T13:00:00+00:00"}
```

Rules the reader enforces:

- it speaks only when the RX gate is closed, nothing is recording, no turn
  or TTS is running, and all that has held for 5 s;
- files older than 10 minutes are dropped unspoken, so a restart never
  replays a backlog;
- files starting with `.` are ignored, so write `.name` and rename it
  (the stub does this).

Replace `due_announcements()` with your own rules: a sensor threshold, a
calendar, a finished job. If the operator answers the announcement, that
answer is an ordinary LLM turn.
