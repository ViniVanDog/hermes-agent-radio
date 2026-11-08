[Português (Brasil)](../10-scanner-dashboard.md) · **English**

# 10. Dashboard and channel-1 scanner

`dashboard.py` is a small stdlib web app (port 8787) with two tabs:

- **Hermes**: the last transcript (STT) and reply (TTS) in large text,
  RX/TX state, a live event history, daily [API usage](11-usage-meter.md), and a
  **Clear context** button. The button sends SIGUSR1 to Hermes, which starts
  a fresh session at the next safe point.
- **Scanner**: drives `radio_scanner.py` on the PC that has the ESP32-C3 on
  USB. It turns the base radio into a slow, receive-only scanner, with
  live audio in the browser, a 0–8 kHz audio spectrogram, a frequency
  timeline and optional WAV capture of voice segments.

```bash
python3 -m baofeng_bridge.web.dashboard --bind 0.0.0.0 --port 8787 [--scanner-url http://<host-pc>:8788]
```

Use the scanner to find a **quiet, legal channel** before you put the agent
on the air ([security and privacy](security-and-privacy.md)). Recording other
people's traffic is restricted in some countries, so keep recording for your
own channels.

## How the scanner works

The BF-777S has no VFO and its programming port cannot change the selected
channel. So the scanner **reprograms channel 1** and you leave the knob on 1:

1. On first start it reads the radio's complete 992-byte image and saves it,
   immutably, as the **Hermes profile** (`diagnostics/scanner/hermes.bin`). It
   refuses to replace that file silently with a different image.
2. For each frequency it rewrites channel 1's 16 bytes (RX frequency,
   **TX inhibited**, narrow FM, no tones, no scramble), uploads the full
   CHIRP-supported block set (48 × 8 bytes) in a fresh programming session,
   then reads all 992 bytes back in another session and compares.
3. It listens. WebRTC VAD runs **locally** on the 16 kHz audio, with an RMS
   floor, vote window and 600 ms settling time, to flag *probable voice*.
   There is no transcription, and nothing goes to Hermes.
4. With no voice it moves on after the listen time. With voice it waits for
   a quiet grace period, or the per-frequency limit.
5. On stop it uploads the Hermes profile and requires all 992 bytes to match
   before re-enabling TX (`a`) and Hermes audio forwarding.

Safety rails:

- While scanning, the C3 is in **receive-only** mode (`r`, persisted in NVS
  across resets). It rejects UDP TX and PTT.
- `udp_relay.py` stops forwarding to Hermes while the scanner state says
  scanning or recovering, **even if the controller crashed**. An unreadable
  state file fails closed.
- Channels 2–16 and settings are rewritten with their saved values. If the
  radio's memory outside channel 1 changed, automatic restore is blocked.
- Communication errors (missing ACK, short reply, bad header) are retried up
  to 3 times with a fresh session. An unexpected radio identity, a profile
  mismatch or a readback mismatch aborts instead. A failed restore keeps TX
  inhibited until you fix it (**Use Hermes profile** retries).
- Every retune is a **persistent memory write**, at least 15 s apart. No
  EEPROM endurance figure is known for this radio. Repeat mode and full-band
  sweeps multiply writes, so think before running them.

### Presets

| Preset | Contents |
|---|---|
| Baofeng factory | The 16 frequencies from our unit's factory image. Edit `FACTORY_FREQUENCIES` in `radio_scanner.py` for yours. |
| Brazil (Anatel) general use | 26 carriers: 462.5625–462.7125 and 467.5625–467.7125 MHz, 12.5 kHz apart (Anatel Ato 14448/2017, §15, Table XII). Replace with your country's licence-free list. |
| Full BF-777S band | 400–470 MHz in 12.5 or 25 kHz steps. 5,601 points at 12.5 kHz means **over 23 h** of listening per pass. The UI shows the estimate before you start. |
| Custom | Up to 256 frequencies on the 12.5 kHz grid inside 400–470 MHz. |

The defaults are a 5 s listen, 3 s grace after voice, 120 s maximum per
frequency and an RMS floor of 250, with repeat off.

## Streaming and recording

Live audio uses Web Audio in the browser (no microphone permission). Frames
carry a 16-byte header (`<dIB3x`: epoch seconds, frequency in Hz, flags) and
640 bytes of PCM. Playback is capped at 600 ms of backlog, with at most 8
listeners. *Record voice segments to the host disk* saves WAV clips (0.5 s
pre-roll, 60 s maximum, split at frequency changes) with JSON sidecars under
the host's `diagnostics/scanner/recordings/`. The default quota is 1024 MB,
and recording pauses when the quota is reached or the disk has less than
32 MB free. Nothing is deleted automatically.

## Setup

```bash
# host PC (ESP32-C3 on USB)
.build-venv/bin/pip install -r requirements-scanner.txt
python3 tools/flash.py --target c3 --port /dev/ttyUSB0
.build-venv/bin/python -m baofeng_bridge.scanner.radio_scanner --bind <host-ip-on-vm-network> \
    --allow-client <vm-ip> --allow-client <host-ip-on-vm-network> \
    [--hermes-profile diagnostics/chirp/<your-backup>.bin]
# or as services: baofeng-scanner + baofeng-relay (systemd/, chapter 5)

# Hermes VM
BAOFENG_SCANNER_URL=http://<host-ip-on-vm-network>:8788 python3 -m baofeng_bridge.web.dashboard --bind 0.0.0.0
# or: systemctl --user enable --now baofeng-dashboard
```

Only the listed client addresses may call the scanner API, and the
dashboard rejects scanner commands from foreign browser origins. Even so,
**keep both on a private network**: the dashboard has no login.

State, results, logs, pre-scan backups and the Hermes profile live in
`diagnostics/scanner/`. Keep that directory when you move the install, and
never delete recovery state to "unstick" Hermes; restore through the
controller instead.

```text
Radio ch.1 → C3 → host udp_relay.py ─┬─ copy → radio_scanner.py (VAD, recording)
                                      └─ Hermes VM (only in the Hermes profile)
VM dashboard.py ⇄ HTTP proxy ⇄ host radio_scanner.py → USB → C3 → radio UART
```
