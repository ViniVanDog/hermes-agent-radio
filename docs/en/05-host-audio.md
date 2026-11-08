[Português (Brasil)](../05-host-audio.md) · **English**

# 5. Host audio

Hermes' voice mode expects a microphone and a speaker. On the Hermes
machine we give it virtual ones: an ALSA loopback card. Received radio
audio is played into the loopback, so Hermes "hears" it; Hermes' TTS is
played into the loopback, and a bridge captures it and sends it to the radio.

```text
ESP32 ──UDP 7777──► host PC: udp_relay.py ──UDP 7777──► Hermes VM: radio_rx.py
                                                         ├─ aplay → hw:HermesLoopback,0,0 ═► Hermes mic (…,1,0)
                                                         └─ writes ~/.local/state/baofeng-radio/rx_gate

Hermes speaker (hw:HermesLoopback,1,0) ═► …,0,0 → arecord → radio_tx_bridge.py ──UDP 7778──► ESP32 (PTT + mic)
```

Linux is required on the machine or VM running Hermes and the audio
bridges, because they use ALSA and the `snd-aloop` module. The physical
PC hosting the VM does not need Linux.

If the ESP32 can reach that machine or VM directly, skip `udp_relay.py`
and point the ESP32's `HOST_PC_IP` at its IP address. If you need a host
relay, `udp_relay.py` uses only the Python standard library; execution on
Windows/macOS has not been verified. The systemd units in this guide
are for Linux.

## 0. Smoke test without Hermes

On the PC named in `secrets.h`:

```bash
python3 -m baofeng_bridge.audio.bridge                      # play RX audio (needs aplay: sudo apt install alsa-utils)
python3 -m baofeng_bridge.audio.bridge --no-play --record received.wav
python3 -m baofeng_bridge.audio.bridge --no-play --utterance-dir utterances   # gate test: one WAV per transmission
```

`bridge.py` uses a 600 ms ALSA buffer to absorb Wi-Fi gaps, and once every
50 packets logs `rx_pps` (target 50), `pcm_hz` (target 16000),
`dropped_window` and `max_gap_ms`.

### The two RX gates

1. **The radio's squelch tone (CTCSS/DCS).** Program the base radio's channel
   with a receive tone and set the same tone on your handhelds. Unrelated
   traffic on the frequency then doesn't open the squelch. This is a noise
   filter, **not security**: anyone can use the same tone, and everyone in
   range still hears both sides ([security and privacy](security-and-privacy.md)).
2. **An energy gate on the PC.** It opens after 3 loud 20 ms packets
   (`--gate-rms`, default 1200) and closes after **3 s of quiet**, so pauses
   inside one transmission stay in one utterance. It keeps 250 ms of
   pre-roll. Raise `--gate-rms` if idle hiss opens it; lower it if quiet
   speakers are missed.

`radio_rx.py` uses the same gate and publishes it to Hermes as a file
(`open|closed <seq> <loud_ms>`). That is what makes Hermes start and stop
recording exactly with each transmission ([chapter 6](06-hermes-integration.md)).

## 1. Host PC: relay to the VM

```bash
python3 -m baofeng_bridge.audio.udp_relay --target-host <vm-ip> --allow-source <esp32-ip>
```

It forwards only valid BF77 packets, and only from the ESP32. While the
[scanner](10-scanner-dashboard.md) owns the radio, it stops forwarding to
Hermes, so the agent never hears scanned channels. It also stops if the
scanner crashed mid-scan.

## 2. Hermes machine: the loopback card

The scripts assume an `snd-aloop` card with id `HermesLoopback`:

```bash
echo 'snd-aloop' | sudo tee /etc/modules-load.d/hermes-loopback.conf
echo 'options snd-aloop id=HermesLoopback pcm_substreams=1' | sudo tee /etc/modprobe.d/hermes-loopback.conf
sudo modprobe snd-aloop id=HermesLoopback pcm_substreams=1
aplay -l | grep -i loopback
```

A loopback card has two devices. Whatever is played on `…,0,0` can be
captured on `…,1,0`, and the other way round. Our bridges use device 0, and
Hermes must use device **1** for both its microphone and its speaker.

Hermes records and plays through PortAudio's default devices, so make
device 1 the default **for the user that runs Hermes**:

- **Plain ALSA, no PulseAudio/PipeWire** (simplest on a headless VM),
  `~/.asoundrc`:

  ```text
  pcm.!default {
      type asym
      playback.pcm "plughw:HermesLoopback,1,0"
      capture.pcm  "plughw:HermesLoopback,1,0"
  }
  ```

- **PipeWire/PulseAudio**: set the loopback's device-1 sink and source as
  defaults (`pactl list short sinks|sources`, then `pactl set-default-sink …`
  and `pactl set-default-source …`).

Check the path end to end before involving Hermes:

```bash
# terminal 1 (bridges stopped): capture what "Hermes" would hear
arecord -D plughw:HermesLoopback,1,0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/heard.wav
# terminal 2: play into the "microphone" side
aplay -D plughw:HermesLoopback,0,0 some.wav
```

## 3. Hermes machine: RX and TX bridges

```bash
python3 -m baofeng_bridge.audio.radio_rx                                   # UDP 7777 → loopback, writes the gate file
python3 -m baofeng_bridge.audio.radio_tx_bridge --esp32-host <esp32-ip>    # loopback → UDP 7778, keys PTT
```

`radio_tx_bridge.py` gates the captured TTS on energy (`--gate-rms 180`).
For each reply it sends `TX_BEGIN` three times, waits for the PTT lead,
then sends exactly one 20 ms packet per 20 ms. The C3's network stack holds
only about six datagrams, so bursts overflow it. Every utterance is capped
at 55 s, under the firmware's 60 s limit.

Both scripts append events to `~/.local/state/baofeng-radio/events.jsonl`
(`rx_open`, `rx_closed`, `tts_audio_open`, `tts_audio_closed`, `tx_begin`,
`tx_playback`, `tx_complete`), which the [dashboard](10-scanner-dashboard.md) and
[usage meter](11-usage-meter.md) read.

## 4. Run everything as user services

```bash
mkdir -p ~/.config/baofeng-radio ~/.config/systemd/user
cp systemd/baofeng.env.example ~/.config/baofeng-radio/baofeng.env    # edit the addresses
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload

# host PC
systemctl --user enable --now baofeng-relay
# Hermes VM
systemctl --user enable --now baofeng-rx baofeng-tx
loginctl enable-linger "$USER"     # keep user services running without a login session
```

The units assume the repo is checked out at `~/hermes-agent-radio`. Edit
`WorkingDirectory=` if it is not.
