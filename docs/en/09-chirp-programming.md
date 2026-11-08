[Português (Brasil)](../09-chirp-programming.md) · **English**

# 9. Reading the radio's memory (CHIRP) through the ESP32-C3

The C3 firmware can turn itself into a USB ↔ radio programming bridge, so
you can back up the radio's channels without a separate programming cable.
The [scanner](10-scanner-dashboard.md) uses the same bridge to retune channel 1.

Verified on a BF-777S that speaks the BF-888 protocol: the radio answered
`P3107`, and two independent implementations (CHIRP's driver and a raw one)
returned the same 992-byte image.

## Wiring

All of this is already wired if you built [chapter 3](03-wiring.md)'s
optional programming jumper:

| Radio contact | C3 |
|---|---|
| 2.5 mm ring (radio data out) | GPIO4, UART1 RX (3.3 V levels) |
| 3.5 mm sleeve (PT / radio data in) | BC548 collector (GPIO10 → 2.2k → base) |
| 2.5 mm sleeve (ground) | GND / BC548 emitter |

GPIO10 has two uses that never overlap: PTT in normal operation, and UART1
TX while programming. UART TX is **inverted in firmware** because the
transistor inverts it again. At programming idle GPIO10 is LOW, so the
transistor is off and PT is released. Never hold PTT during the handshake.

## What the bridge does

Serial command `b` (or `q` for the scanner's 1 s variant):

1. refuses if a transmission is active or queued;
2. pauses audio capture and suspends the TX task;
3. gives UART1 GPIO10 (TX) and GPIO4 (RX) at **9600 8N1**;
4. prints `CHIRP READY`. From then on the USB console (still **115200**) carries raw bytes.

After 15 s without data (1 s for `q`), or 2 minutes in total, it sends the
radio's exit byte, restores GPIO10 as released PTT, discards stale TX
packets and resumes audio. It prints `CHIRP CLOSED; audio resumed; PTT OFF.`
plus GPIO edge traces for diagnosis.

That message confirms the **C3** cleaned up. It does not prove the radio
left programming mode (see below).

## Download a backup

Close every serial monitor first.

```bash
# with CHIRP's driver (CHIRP installed via pipx): saves .img, .bin, .csv and a log
~/.local/share/pipx/venvs/chirp/bin/python tools/chirp_download.py --port /dev/ttyUSB0

# independent raw implementation, pyserial only
.build-venv/bin/pip install pyserial
.build-venv/bin/python tools/chirp_download.py --raw --port /dev/ttyUSB0
```

Files go to the git-ignored `diagnostics/chirp/`. The helper permits only
identification, block reads, ACKs and exit. **It cannot write to the radio.**
It handles the CH340 reset on port open by resending `b`, retries
`CHIRP BUSY` every 2 s for up to 60 s, and waits for `CHIRP CLOSED` before
closing the port.

The desktop CHIRP app cannot use the port directly. It would need a proxy
that sends `b` and keeps the host side at 115200.

## The protocol (BF-888 family)

```text
host → radio  02            wait 100 ms
host → radio  "PROGRAM"     (50 52 4f 47 52 41 4d)
radio → host  06            ACK
host → radio  02
radio → host  "P3107" + version bytes (50 33 31 30 37 f7 00 00)
host → radio  06
radio → host  06
read:  52 hi lo 08  →  57 hi lo 08 + 8 data bytes, then ACK; addresses 0x0000–0x03D8
exit:  45 ("E")
```

On success the radio's LED flashed green for roughly a second. Failed
attempts lacked that flash and returned no ACK. This is a useful physical
clue, not a measurement.

## If the radio seems stuck in programming mode

The exit byte gets no reply in this protocol. If the radio still behaves
oddly, send `E` through the bridge (without `02`/`PROGRAM`), let the bridge
close, and check the radio. If it is still stuck, **power-cycle the radio**:
resetting the C3 does not reset a battery-powered radio.

## Honest notes

- Our first bridge build got no answer from the radio with either
  implementation. The next build added GPIO edge tracing on GPIO10/GPIO4,
  and both downloads then worked. The tracing's interrupt setup also enables
  the input buffer on each traced pin, including the UART TX pin, so the
  change was not purely logging. **The cause of the early failures was never
  isolated.** Keep the tracing build as is unless you run a controlled
  comparison.
- `--slow-handshake` (10 ms between `PROGRAM` bytes) did not help. The
  successful reads used normal timing.
- Edge traces show activity at the C3 pins, not signal quality at the radio
  connector.
