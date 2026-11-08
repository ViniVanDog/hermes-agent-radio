[Português (Brasil)](../02-choosing-a-board.md) · **English**

# 2. Choosing a board

We built this with what was on the bench: first an **ESP32 DevKitC V4**
(classic ESP32), then an **ESP32-C3-MINI-1**. Both work and both
firmwares are in this repo. Neither is special. The analogue circuit in
[chapter 3](03-wiring.md) and the two UDP protocols in [chapter 4](04-firmware.md)
are the real interface. A different board needs a firmware port; the host
side does not care which board sends the packets.

## What any board must do

| Requirement | Why | Classic ESP32 | ESP32-C3 |
|---|---|---|---|
| 2.4 GHz Wi-Fi + UDP | Audio goes to the PC as 20 ms UDP packets | ✓ | ✓ |
| ADC sampled at a steady **16 kHz**, while Wi-Fi is on | RX audio. Needs DMA or a hardware timer; a software `analogRead()` loop jitters under Wi-Fi load | ADC1 via DMA at 20 kHz, keeping 4 of 5 samples | same |
| An ADC channel that still works with Wi-Fi on | On the classic ESP32, **ADC2 is unusable while Wi-Fi runs** | ADC1 only (GPIO32–39) | ADC1 (GPIO0–4) |
| An analogue-ish TX output updated at 16 kHz | Speech into the radio's mic input | 8-bit DAC (GPIO25) | No DAC: 8-bit PWM, 156.25 kHz carrier |
| One spare GPIO | PTT through an NPN transistor | ✓ | ✓ |
| Optional: a UART whose TX can be inverted | [Radio programming](09-chirp-programming.md) through the same NPN | not implemented | ✓ UART1, inverted TX |
| 3.3 V logic, USB serial | Flashing, diagnostics, programming bridge | ✓ | ✓ |

If you port to another MCU (RP2040 W, ESP32-S3, …), keep these:

- 16 kHz, signed 16-bit, mono PCM, 320 samples per packet;
- PTT **off** at boot, before Wi-Fi starts, and released on every error path;
- the PTT watchdog: no TX audio for 2 s, or 60 s total, releases PTT.

## The two boards we used

### ESP32-C3-MINI-1 (current build, `firmware/BaofengRxC3`)

| GPIO | Function |
|---|---|
| GPIO3 | RX audio in (ADC1_CH3) from the S+ conditioning circuit |
| GPIO1 | TX audio out: 8-bit PWM, 156.25 kHz carrier, into the 33k/680R/2.2 µF network |
| GPIO10 | NPN base (via 2.2k): PTT, **and** inverted UART TX while programming |
| GPIO4 | Programming UART RX from the radio's 2.5 mm ring (optional) |

### ESP32 DevKitC V4 (first build, `firmware/BaofengRx`)

| GPIO | Function |
|---|---|
| GPIO34 | RX audio in (ADC1_CH6) |
| GPIO25 | TX audio out: DAC1, updated by a 16 kHz hardware timer |
| GPIO26 | NPN base (via 2.2k): PTT |

On the classic ESP32, I2S0 belongs to the RX ADC-DMA driver. TX writes DAC1
directly from a separate timer, so the two directions never fight over a
peripheral.

Build either one with `tools/flash.py` ([chapter 4](04-firmware.md)): `--target c3`
for the C3, or no `--target` for the classic ESP32.
