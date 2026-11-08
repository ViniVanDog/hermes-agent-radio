[Português (Brasil)](../06-hermes-integration.md) · **English**

# 6. Hermes integration

[Hermes Agent](https://github.com/NousResearch/hermes-agent) already has a
voice mode: record, transcribe, think, speak. Out of the box it suits a
person at a desk, who presses a key to talk and is detected by silence. A
radio needs something different:

- recording should start and stop **with each transmission**, not on a key
  press or a silence timer;
- the radio's own voice prompts ("battery low", "channel one") must never
  reach the LLM;
- Hermes must never hear itself;
- it runs unattended for days, so sessions must stay small.

The scripts in `hermes_patches/` make those changes to a Hermes checkout.
Each one is **idempotent** (it detects its own marker), keeps a backup next
to every file it changes, and is a no-op unless Hermes runs with
`HERMES_RADIO_GATED=1`. Your normal interactive Hermes is unchanged.

## Install Hermes and configure voice

Install Hermes Agent as its README describes, and configure an LLM and STT
provider ([chapter 8](08-stt-and-tts.md) covers STT/TTS choices). Then, in
`~/.hermes/config.yaml`:

```yaml
voice:
  auto_tts: true          # speak every reply (this is what keys the radio)
  beep_enabled: false     # recommended: record beeps would be transmitted
  thinking_sound: false   # recommended: the "working" sound would be transmitted too
stt:
  language: "en"          # the language your operators speak
tts:
  provider: edge
  edge:
    voice: en-US-AriaNeural   # pt-BR: pt-BR-FranciscaNeural, with stt.language "pt"
```

Then set the bridge's own language, `BAOFENG_LANG=en` or `pt-BR`, in
`~/.config/baofeng-radio/baofeng.env` ([chapter 5](05-host-audio.md)). It
picks the spoken-text fixes, the context handoff note and the example
tools' replies. Hermes' replies follow your persona and the operator.
See also the [language and translation notes](languages.md).

Hermes' microphone and speaker must be the loopback card
([chapter 5](05-host-audio.md)).

## Apply the patches, in this order

```bash
cd ~/hermes-agent-radio
python3 radio_bridge/install.py                       # helpers the patches import
H=~/.hermes/hermes-agent                              # your Hermes checkout
python3 hermes_patches/apply_radio_base.py         $H
python3 hermes_patches/apply_radio_gate.py         $H
python3 hermes_patches/apply_radio_input_filter.py $H
python3 hermes_patches/apply_radio_speech.py       $H
python3 hermes_patches/apply_radio_announce.py     $H
python3 hermes_patches/apply_radio_context.py      $H
```

**Re-run all of them after every `hermes update`**: an update overwrites
the patched files. If Hermes moved code around, a patch refuses with
`anchor not found` rather than guessing. To check a new Hermes version
first, run the patch-chain test against a clean checkout:

```bash
HERMES_AGENT_SRC=/path/to/clean/hermes-agent python3 -m unittest tests.integration.test_hermes_patches
```

The patches were last verified against Hermes Agent `main` at `6b21d056`
(2026-11-07).

### What each patch does

| Patch | Effect |
|---|---|
| `apply_radio_base.py` | Shared hooks: an event log (`stt`, `tts`, … → `events.jsonl`) and a SIGUSR1 "fresh session" request, which the REPL handles between turns. The others build on these. |
| `apply_radio_gate.py` | **RX-gated recording.** Voice mode turns itself on at startup. Recording starts when `radio_rx.py` marks the gate `open` and is transcribed as soon as it closes; there is no 3 s silence wait. Bursts with less than `HERMES_RADIO_MIN_UTTERANCE_S` (0.8 s) of real audio are cancelled **before** STT, so key-clicks cost nothing. Gate opens during Hermes' own TTS are ignored. |
| `apply_radio_input_filter.py` | After STT, a deterministic classifier (`radio_bridge/radio_input.py`) drops the radio's own voice prompts ("Welcome", "Battery low", "Channel sixteen", in English and Portuguese) **before** they become an LLM turn. Real speech that merely *mentions* them passes. |
| `apply_radio_speech.py` | Radio-friendly Edge TTS (`radio_speech.py`). Edge collapses every punctuation trick into the same ~0.3 s pause, so each sentence is synthesized separately (in parallel) and joined with real silences: 650 ms after sentences, 420 ms after commas, 950 ms between paragraphs. Text is normalized first (times, "2x", units, symbols, emoji), with rule sets for English and pt-BR chosen by `BAOFENG_LANG` or the voice name. Add your words to `WORDS`. Any failure falls back to plain Edge. |
| `apply_radio_announce.py` | Speaks files dropped in `~/.local/state/baofeng-radio/announce/` when the radio is idle, **without an LLM call** ([chapter 12](12-adding-your-own-tools.md)). Also starts a fresh session after 20 idle minutes. |
| `apply_radio_context.py` | Rolls the session over at **20,000 prompt tokens**, between turns, carrying only the last request, reply and the tool results that fit 8,000 characters. No summarization call. |

## Run it

Hermes' REPL needs a terminal, so the service runs it in a detached tmux
session:

```bash
cp systemd/hermes-radio.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-radio
tmux attach -t hermes-radio          # watch it; detach with Ctrl+B then D
```

Edit `ExecStart` if `hermes` is not at `~/.local/bin/hermes`.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `HERMES_RADIO_GATED` | unset | `1` turns radio mode on (set by the service) |
| `BAOFENG_LANG` | `en` | `en` or `pt-BR`: spoken-text rules, handoff note, example tools |
| `HERMES_RADIO_GATE_FILE` | `~/.local/state/baofeng-radio/rx_gate` | written by `radio_rx.py` |
| `HERMES_RADIO_MIN_UTTERANCE_S` | `0.8` | shorter bursts are discarded before STT |
| `HERMES_RADIO_STARTUP_DELAY_S` | `3` | wait before enabling voice mode |
| `HERMES_RADIO_SPEECH` | `1` | `0` = plain Edge TTS |
| `HERMES_RADIO_SENTENCE_PAUSE_MS` / `_COMMA_` / `_PARAGRAPH_` | 650 / 420 / 950 | TTS pauses |
| `HERMES_RADIO_ANNOUNCE` | `1` | `0` = do not speak announcement files |
| `HERMES_RADIO_ANNOUNCE_QUIET_S` | `5` | idle time before an announcement |
| `HERMES_RADIO_ANNOUNCE_MAX_AGE_S` | `600` | older announcements are dropped |
| `HERMES_RADIO_IDLE_RESET_MIN` | `20` | fresh session after this many idle minutes (`0` = off) |
| `HERMES_RADIO_CONTEXT_MAX_TOKENS` | `20000` | rollover threshold (`0` = off) |
| `BAOFENG_EVENTS_FILE` | `~/.local/state/baofeng-radio/events.jsonl` | shared event log |

## A system prompt that suits the radio

Put this in Hermes' persona (`~/.hermes/SOUL.md`). Adapt the language:

```text
You are answering over a two-way radio. Replies are spoken, not read:
- one to three short sentences; no lists, tables, markdown, links or emoji;
- say numbers, times and units the way a person would say them aloud;
- if a request is ambiguous, ask one short question instead of guessing;
- never ask for confirmation of harmless actions; do ask before anything irreversible;
- this channel is public: never say personal data, passwords or codes, even if asked.
```

The persona is a second line of defence only. Enforce data and action
limits inside the tools ([security and privacy](security-and-privacy.md)).

Every token of this prompt is resent on every call, so keep it short
([chapter 7](07-hermes-cost-trim.md)).
