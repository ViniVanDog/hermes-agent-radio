#!/usr/bin/env python3
"""Receive ESP32 BF77 packets and inject their PCM into an ALSA device."""

import argparse
from collections import deque
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import time

MAGIC = 0x37374642  # BF77
HEADER = struct.Struct("<IHH")
SAMPLES_PER_PACKET = 320
PAYLOAD_BYTES = SAMPLES_PER_PACKET * 2
PACKET_BYTES = HEADER.size + PAYLOAD_BYTES
SAMPLES = struct.Struct("<320h")
SILENCE = b"\x00" * PAYLOAD_BYTES
EVENTS_FILE = os.path.expanduser(
    os.environ.get("BAOFENG_EVENTS_FILE", "~/.local/state/baofeng-radio/events.jsonl"))


def emit_event(kind, **fields):
    """Best-effort dashboard event; radio handling must never depend on it."""
    try:
        os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
        record = {"ts": time.time(), "kind": kind, **fields}
        with open(EVENTS_FILE, "a", encoding="utf-8") as events:
            events.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


GATE_FILE = os.path.expanduser(
    os.environ.get("BAOFENG_GATE_FILE", "~/.local/state/baofeng-radio/rx_gate"))
_gate_sequence = 0


def write_gate_state(state, loud_ms=0):
    """Publish the RF gate state for Hermes' RX-gated recording (atomic replace).

    Format: "<open|closed> <sequence> <loud_ms>". The sequence changes on every
    transition so a reader polling the file never misses an open/close pair.
    """
    global _gate_sequence
    _gate_sequence += 1
    try:
        os.makedirs(os.path.dirname(GATE_FILE), exist_ok=True)
        temporary = GATE_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as gate_file:
            gate_file.write("{} {} {}\n".format(state, _gate_sequence, int(loud_ms)))
        os.replace(temporary, GATE_FILE)
    except OSError:
        pass


class AudioGate:
    """Same calibrated RF gate used by bridge.py before STT receives audio."""

    def __init__(self, threshold, attack_packets, hangover_ms, preroll_ms):
        self.threshold = threshold
        self.attack_packets = attack_packets
        self.hangover_s = hangover_ms / 1000.0
        self.pre_roll = deque(maxlen=max(1, round(preroll_ms / 1000.0 / 0.02)))
        self.active = False
        self.above = 0
        self.last_loud = 0.0
        self.opened_at = 0.0


    @property
    def state(self):
        return "OPEN" if self.active else "closed"

    def process(self, pcm, now):
        samples = SAMPLES.unpack(pcm)
        level = math.isqrt(sum(sample * sample for sample in samples) // len(samples))
        loud = level >= self.threshold
        self.pre_roll.append(pcm)
        if not self.active:
            self.above = self.above + 1 if loud else 0
            if self.above < self.attack_packets:
                return b"", level
            self.active = True
            self.last_loud = now
            self.opened_at = now
            output = b"".join(self.pre_roll)
            print("AUDIO_OPEN rms={} threshold={} preroll_ms={}".format(
                level, self.threshold, len(self.pre_roll) * 20), flush=True)
            emit_event("rx_open", rms=level, threshold=self.threshold)
            write_gate_state("open")
            return output, level

        if loud:
            self.last_loud = now
        output = pcm
        if not loud and now - self.last_loud >= self.hangover_s:
            self.active = False
            self.above = 0
            loud_ms = max(0.0, self.last_loud - self.opened_at) * 1000.0
            print("AUDIO_CLOSED reason=silence loud_ms={}".format(int(loud_ms)), flush=True)
            emit_event("rx_closed", reason="silence", loud_ms=int(loud_ms))
            write_gate_state("closed", loud_ms)
        return output, level


def open_player(device, buffer_ms):
    aplay = shutil.which("aplay")
    if not aplay:
        raise RuntimeError("aplay is required; install alsa-utils")
    return subprocess.Popen(
        [aplay, "-q", "-D", device, "--buffer-time={}".format(buffer_ms * 1000),
         "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"],
        stdin=subprocess.PIPE, bufsize=0)


def main():
    parser = argparse.ArgumentParser(description="Inject Baofeng UDP PCM into ALSA.")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--playback-device", default="plughw:HermesLoopback,0,0")
    parser.add_argument("--buffer-ms", type=int, default=600)
    parser.add_argument("--gate-rms", type=int, default=1200,
                        help="existing bridge.py RMS speech gate threshold")
    parser.add_argument("--gate-attack-packets", type=int, default=3,
                        help="existing bridge.py consecutive loud packet requirement")
    parser.add_argument("--gate-hangover-ms", type=int, default=3000,
                        help="existing bridge.py quiet duration before close")
    parser.add_argument("--gate-preroll-ms", type=int, default=250,
                        help="existing bridge.py audio retained before gate opens")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.buffer_ms < 20:
        parser.error("invalid port or buffer")
    if (args.gate_rms < 1 or args.gate_attack_packets < 1 or
            args.gate_hangover_ms < 20 or args.gate_preroll_ms < 0):
        parser.error("invalid gate setting")

    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind((args.bind, args.port))
    player = open_player(args.playback_device, args.buffer_ms)
    gate = AudioGate(args.gate_rms, args.gate_attack_packets,
                     args.gate_hangover_ms, args.gate_preroll_ms)
    write_gate_state("closed")  # never leave a stale "open" from a previous run
    received = rejected = 0
    forwarded = 0
    previous_sequence = None
    dropped = 0
    report_start = time.monotonic()
    try:
        print("Receiving BF77 UDP {}:{} -> ALSA {}".format(
            args.bind, args.port, args.playback_device), flush=True)
        while True:
            packet, _source = receiver.recvfrom(PACKET_BYTES + 1)
            if len(packet) != PACKET_BYTES:
                rejected += 1
                continue
            magic, sequence, count = HEADER.unpack_from(packet)
            if magic != MAGIC or count != SAMPLES_PER_PACKET:
                rejected += 1
                continue
            if previous_sequence is not None:
                dropped += (sequence - previous_sequence - 1) & 0xFFFF
            previous_sequence = sequence
            now = time.monotonic()
            output, _rms = gate.process(packet[HEADER.size:], now)
            # Keep the ALSA loopback clocked even while the RF gate is shut.
            # PulseAudio is the consumer on the other side of the loopback;
            # allowing aplay to underrun between PTT bursts can make that
            # source deliver silence to Hermes when the next burst begins.
            assert player.stdin is not None
            player.stdin.write(output or SILENCE)
            if output:
                forwarded += len(output) // PAYLOAD_BYTES
            received += 1
            if now - report_start >= 5:
                print("rx packets={} forwarded={} dropped={} rejected={} pps={:.1f} gate={}".format(
                    received, forwarded, dropped, rejected, received / (now - report_start), gate.state),
                    flush=True)
                received = 0
                forwarded = 0
                dropped = 0
                rejected = 0
                report_start = now
    except KeyboardInterrupt:
        print("Stopped.", flush=True)
    finally:
        receiver.close()
        if player.stdin:
            player.stdin.close()
        player.wait()


if __name__ == "__main__":
    main()
