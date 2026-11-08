[Português (Brasil)](../08-stt-and-tts.md) · **English**

# 8. Speech-to-text and text-to-speech

On the radio, **latency is the experience**. The operator releases PTT and
waits in silence, and every second of STT is a second of dead air before
the agent even starts thinking. Pick the engines with that in mind.

## STT: what Hermes uses

The Hermes path ([chapter 6](06-hermes-integration.md)) uses Hermes' own STT
setting. Hermes supports several providers (`stt.provider` in `config.yaml`):
cloud ones such as xAI, Groq, OpenAI, Mistral and DeepInfra, and `local`
(faster-whisper). We run a **cloud** provider (xAI). Because the RX gate
cuts each utterance exactly at the end of the transmission, the upload is
short and the transcript comes back quickly.

Tips that apply whatever you pick:

- Set `stt.language` explicitly. Auto-detection on a few seconds of
  band-limited radio audio guesses wrong more often.
- A vocabulary hint (`stt.prompt`, where the provider supports it) helps with
  call signs, names and jargon.
- Radio audio is 300–3000 Hz and noisy. Large models handle it better than
  tiny ones; assess accuracy alongside latency.

## Local Whisper in Hermes: optional, with more latency

**We recommend cloud STT for the radio agent**, prioritizing low latency.
Local Whisper is an alternative when audio must stay on the machine, but
it is not our recommendation for conversations that need quick replies.

If you need that alternative, use Hermes' own `local` provider
(`stt.provider` in `config.yaml`) and set `stt.language` explicitly.
Audio still arrives through the same ALSA loopback; this radio path needs
no additional STT worker or installer.

Measure end-to-end latency on the hardware you will use. Larger models can
delay replies, especially on CPU; smaller models trade accuracy for speed.
Processing also competes with the machine's other tasks for resources.

## TTS

Hermes' default **Edge TTS** is free, needs no key and sounds good on a
radio. With [`apply_radio_speech.py`](06-hermes-integration.md) it also gets
real pauses between sentences, which matter a lot when the listener can't
re-read. Pick a voice in `tts.edge.voice`. Any other Hermes TTS provider
works too; the radio only sees audio on the loopback.

### Voice cloning outside Hermes (`tx.py`)

`tx.py` sends speech to the radio without Hermes. It is handy for testing,
or for scripts that only need to speak:

```bash
python3 -m baofeng_bridge.audio.tx --esp32-host <esp32-ip> --wav hello.mp3 --begin-repeats 3       # any audio file
python3 -m baofeng_bridge.audio.tx --esp32-host <esp32-ip> "Base online." --begin-repeats 3      # via Chatterbox
```

The text mode calls a local [Chatterbox](https://github.com/resemble-ai/chatterbox)
server (`--chatterbox-url`, default `http://127.0.0.1:8000`, endpoint
`/v1/audio/speech/clone`) with your own reference voice
(`--voice voices/reference.mp3`; `voices/` is git-ignored). From Python:

```python
from baofeng_bridge.audio.tx import synthesize_and_transmit
synthesize_and_transmit("<esp32-ip>", "Base online.", begin_repeats=3)
```
