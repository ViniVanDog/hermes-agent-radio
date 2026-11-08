[Português (Brasil)](../01-materials.md) · **English**

# 1. Materials

Everything here is common hobby stock. Values are the ones in our working
build. Where a value is a starting point you are expected to tune, the table
says so.

## Radio side

| Qty | Part | Notes |
|---|---|---|
| 1 | Baofeng handheld with the 2-pin Kenwood (K1) jack | We used a **BF-777S** (programs with the BF-888 protocol). UV-5R/BF-888 class radios share the K1 pinout. |
| 1 | K1 plug or cable | Easiest: sacrifice a cheap Baofeng earpiece/headset and cut the cable. Or buy bare 2.5 mm + 3.5 mm plugs. Cheap headsets often leave the programming-data and +V conductors unconnected; you need them only for [programming](09-chirp-programming.md). |
| 1 | Second radio on the same channel | For testing: you talk on it, the base Baofeng receives. Program **both** off the factory channels ([why](security-and-privacy.md)). |

## Interface board

| Qty | Part | Where it goes | Why |
|---|---|---|---|
| 1 | Wi-Fi microcontroller | — | See [choosing a board](02-choosing-a-board.md). |
| 1 | 2.2 µF electrolytic | RX: speaker S+ → ADC | Blocks the radio's DC; passes audio. |
| 1 | 33 kΩ | RX: series into the ADC pin | Limits current; sets the level. **Tune this one** if RX clips or is too quiet. |
| 2 | 27 kΩ | RX: bias divider 3.3 V / GND | Centres the ADC input at ~1.65 V. Keep the two equal. |
| 1 | 33 kΩ | TX: DAC/PWM pin → TX node | Upper leg of the attenuator. |
| 1 | 680 Ω | TX: TX node → GND | Lower leg; ~1:50 attenuation down to mic level. |
| 1 | 2.2 µF electrolytic | TX: TX node → mic M+ | Coupling. Polarity matters: **+ goes to the radio** (the mic input has positive DC bias). |
| 1 | BC548 (or any small NPN: 2N3904, BC547, 2N2222) | PTT | Pulls PT to ground. **Never drive PT from a GPIO directly.** |
| 1 | 2.2 kΩ | GPIO → NPN base | Base resistor. |
| 1 | Breadboard or perfboard, jumpers | — | Our build is a 30-row breadboard. |

Optional, for PWM-only boards (like the ESP32-C3): a small RC low-pass
on the PWM output, after the 33k/680R network, if you hear carrier whine.
Our C3 build runs a 156.25 kHz carrier through the same 33k/680R/2.2 µF
network as the DAC build, with no extra filter.

Optional, for [programming](09-chirp-programming.md): one jumper from the
2.5 mm ring (radio data out) to a UART RX pin. Levels must be 3.3 V
compatible. Check yours with a meter before connecting.

## Computer side

- **A computer with Python 3.10+ and USB access to the ESP32**, to build
  and flash the firmware. If using a VM, pass the USB device through to it.
- **A Linux machine or VM running Hermes Agent with network access to the ESP32.**
  Linux is required for ALSA audio (`alsa-utils` and the `snd-aloop` kernel
  module), not for the physical PC operating system. We also use `tmux` and
  `ffmpeg` (see [host audio](05-host-audio.md)). We run Hermes in a libvirt
  VM to isolate it from the host.
- Accounts/keys for whatever LLM and STT providers you configure in Hermes.
  Edge TTS (Hermes' default) needs no key.

## Tools

- Multimeter: check bias (~1.65 V), polarity and plug pinout.
- Soldering iron if you use perfboard or cut a headset cable.
- Optional: an oscilloscope makes TX level tuning quicker, but the serial
  diagnostics in [chapter 4](04-firmware.md) are enough.
