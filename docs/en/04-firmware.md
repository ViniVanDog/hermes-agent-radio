[Português (Brasil)](../04-firmware.md) · **English**

# 4. Firmware

Both firmwares are native Arduino C++ built with PlatformIO, for reliable
ADC and Wi-Fi timing. Python runs only on the PC.

| Target | Source | Build |
|---|---|---|
| ESP32-C3-MINI-1 | `firmware/BaofengRxC3/` | `python3 tools/flash.py --target c3 …` |
| Classic ESP32 (DevKitC) | `firmware/BaofengRx/` | `python3 tools/flash.py …` |

## 1. Wi-Fi and the PC address

```bash
cp firmware/BaofengRx/secrets.example.h firmware/BaofengRx/secrets.h
```

Edit `secrets.h` (it is git-ignored; both firmwares include it):

- `WIFI_NETWORKS`: one or more SSID/password pairs, tried in order.
- `HOST_PC_IP`: the LAN address of the PC that receives RX audio. It is never
  `127.0.0.1`, because on the ESP32 that is the ESP32 itself.
- `START_AUDIO_STREAM`: `false` keeps UDP off until you type `u`, which is
  handy for checking ADC levels first.

## 2. Build, upload, monitor

```bash
python3 tools/flash.py --target c3 --port /dev/ttyUSB0 --monitor
```

On first run `tools/flash.py` creates a private `.build-venv` with PlatformIO
(it never touches the system Python), downloads the toolchain, builds,
uploads, and opens a 115200-baud monitor. Useful variants:

```bash
python3 tools/flash.py --build-only            # compile only
python3 tools/flash.py --port COM3             # Windows
sudo apt install python3-venv            # if venv creation fails on Debian/Ubuntu
```

## 3. Serial commands

| Key | Both | Meaning |
|---|---|---|
| `u` | ✓ | start UDP audio streaming |
| `s` | ✓ | stop UDP; ADC statistics keep running |
| `c` | ✓ | re-measure the quiet-input DC centre (1 s, keep the radio silent) |
| `p` | ✓ | **PTT-only test**: key for 3 s, no audio |
| `t` | ✓ | **tone test**: PTT, 1.5 s lead, 1 s low-level 1 kHz tone, 150 ms tail, release |
| `h` | ✓ | help |
| `b` | C3 | radio programming bridge, 15 s idle timeout ([chapter 9](09-chirp-programming.md)) |
| `q` | C3 | programming bridge, 1 s idle timeout (scanner retunes) |
| `r` | C3 | scanner profile: persistently inhibit all TX (survives reset, stored in NVS) |
| `a` | C3 | re-enable TX after the radio memory is restored |

## 4. Reading the RX diagnostics

```text
RX 100 ms: min=... max=... avg=... dc=... p-p=... n=... clipping=none udp=on ...
```

- Quiet channel: `min`, `max` and `avg` sit near `dc`, and `p-p` is small.
- Someone talking: `min` drops, `max` rises, and `p-p` gets much larger.
- `clipping=LOW/HIGH` means samples came within 64 counts of 0 or 4095.
  Speech should show `clipping=none`. Adjust the RX **33k** ([chapter 3](03-wiring.md)),
  not the bias resistors.

Timing comes from ADC **DMA**, not an `analogRead()` loop. The driver's
minimum DMA rate is 20 kHz, so the firmware captures at 20 kHz and keeps
4 of every 5 samples, giving exactly 16,000 samples/s. With UDP on, long
stat lines print once per second instead of ten times. Health targets:

```text
frame_start_hz         ≈ 50 (20 ms frames)
frame_period_us_avg    ≈ 20000
adc_dma_ovf, short_frames, adc_dma_errors   0
```

## 5. RX packet format (ESP32 → PC, UDP 7777)

One 648-byte datagram every 20 ms:

```text
uint32 magic     0x37374642  ("BF77", little-endian)
uint16 sequence  wraps at 65535
uint16 count     320
int16  pcm[320]  16 kHz mono, signed little-endian
```

## 6. TX packet format (PC → ESP32, UDP 7778)

Every datagram starts with a 10-byte little-endian header:

```text
uint32 magic          0x58544642  ("BFTX")
uint8  type           1 = TX_BEGIN, 2 = TX_AUDIO, 3 = TX_END
uint8  reserved       0
uint16 sequence       increments every packet
uint16 payload_bytes  0 for BEGIN/END, 640 for AUDIO (320 signed 16-bit samples)
```

The sender sends `TX_BEGIN`, waits **1.5 s** (the firmware's PTT lead, so the
radio is keyed before speech arrives), streams AUDIO at real-time pace, then
sends `TX_END`. The firmware buffers 120 ms before playing and queues up to
480 ms. It logs starts, buffer depth, sequence gaps, underruns and abort
reasons, but nothing per sample. For the C3, send `TX_BEGIN` three times
with the same sequence number (`python3 -m baofeng_bridge.audio.tx --begin-repeats 3`;
`radio_tx_bridge.py` does this by default), so one lost UDP packet does not
lose the whole reply.

While transmitting, the firmware keeps draining RX DMA but **does not
forward RX audio**, so the agent never hears itself.

### Fail-safes (PTT is always released)

- PTT is driven off at boot, before Wi-Fi or audio start.
- Released on: Wi-Fi loss, malformed packet mid-stream, DMA/timer failure,
  queue overflow, **2 s without audio** (`TX_TIMEOUT_MS`), or **60 s**
  total (`TX_MAX_TRANSMIT_MS`).
- States: `IDLE → TX_STARTING → TRANSMITTING → TX_ENDING`.

Key constants sit together at the top of each `.ino`:

```text
PCM_SAMPLE_RATE_HZ = 16000    PTT_LEAD_MS = 1500    PTT_TAIL_MS = 150
TX_TIMEOUT_MS = 2000          TX_MAX_TRANSMIT_MS = 60000
classic: TX_DAC_AMPLITUDE = 128    C3: TX_PWM_AMPLITUDE = 128, TX_PWM_CARRIER_HZ = 156250
```

## 7. First TX test

Put a second radio on the same channel, open the monitor, then:

1. `p`: the second radio should show a carrier for 3 s (silent).
2. `t`: you should hear a quiet 1 kHz tone.
3. Only then send real audio:

```bash
python3 -m baofeng_bridge.audio.tx --esp32-host <esp32-ip> --wav some-speech.mp3 --begin-repeats 3
```

`tx.py` decodes with FFmpeg from `.build-venv` (`.build-venv/bin/pip install imageio-ffmpeg`
if that venv was recreated). It never plays audio on the PC.

### TX troubleshooting

| Symptom | Check |
|---|---|
| Carrier, no tone | TX pin → 33k → **capacitor polarity** → M+; shared ground |
| No carrier | NPN pinout, 2.2k base resistor, `p` logs both PTT transitions |
| Very quiet | 680R and capacitor wiring first; the amplitude constant is already at full 8-bit scale |
| Distorted / over-deviated | Lower the amplitude constant (e.g. 128 → 96 → 64), one flash at a time |
| First or last words clipped | Raise `PTT_LEAD_MS` or `PTT_TAIL_MS` |
| TX stops early | Serial shows `TX aborted`; `stream timeout` means the sender stalled > 2 s |
