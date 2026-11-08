#!/usr/bin/env python3
"""Receive BF-777S PCM UDP packets, play them, and create gated utterance WAVs.

The Baofeng's configured CTCSS/DTCS decoder is the radio-level receive gate.
This program adds a speech-energy gate and can save one WAV per spoken
transmission for audio diagnostics.
"""

import argparse
from collections import deque
from datetime import datetime
import math
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time
import wave


MAGIC = 0x37374642  # Little-endian bytes: BF77
HEADER = struct.Struct("<IHH")
SAMPLE_RATE = 16000
SAMPLES_PER_PACKET = 320
FRAME_DURATION_S = SAMPLES_PER_PACKET / SAMPLE_RATE
PAYLOAD_BYTES = SAMPLES_PER_PACKET * 2
PACKET_BYTES = HEADER.size + PAYLOAD_BYTES
SAMPLE_STRUCT = struct.Struct("<{}h".format(SAMPLES_PER_PACKET))


def pcm_rms(pcm):
    """Return integer RMS amplitude for one signed 16-bit PCM packet."""
    samples = SAMPLE_STRUCT.unpack(pcm)
    return math.isqrt(sum(sample * sample for sample in samples) // len(samples))


def open_player(enabled, buffer_ms):
    if not enabled:
        return None
    aplay = shutil.which("aplay")
    if aplay is None:
        raise SystemExit(
            "Playback needs aplay. Install alsa-utils, or pass --no-play to receive only."
        )
    return subprocess.Popen(
        [
            aplay,
            "-q",
            "--buffer-time={}".format(buffer_ms * 1000),
            "-f",
            "S16_LE",
            "-r",
            str(SAMPLE_RATE),
            "-c",
            "1",
        ],
        stdin=subprocess.PIPE,
        bufsize=0,
    )


def open_recording(path):
    if path is None:
        return None
    recording = wave.open(path, "wb")
    recording.setnchannels(1)
    recording.setsampwidth(2)
    recording.setframerate(SAMPLE_RATE)
    return recording


class AudioGate:
    """PCM-energy gate with attack, hangover, pre-roll, and utterance WAV output."""

    def __init__(self, threshold, attack_packets, hangover_ms, preroll_ms,
                 min_utterance_ms, output_dir):
        self.threshold = threshold
        self.attack_packets = attack_packets
        self.hangover_s = hangover_ms / 1000.0
        self.min_utterance_s = min_utterance_ms / 1000.0
        self.pre_roll = deque(maxlen=max(1, round(preroll_ms / 1000.0 / FRAME_DURATION_S)))
        self.output_dir = Path(output_dir) if output_dir else None
        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)

        self.active = False
        self.above_threshold_packets = 0
        self.last_loud_at = 0.0
        self.started_at = 0.0
        self.utterance = bytearray()
        self.utterance_number = 0

    @property
    def state(self):
        return "OPEN" if self.active else "closed"

    def _write_utterance(self, ended_at, reason):
        duration_s = len(self.utterance) / (SAMPLE_RATE * 2)
        saved_path = None
        if duration_s >= self.min_utterance_s and self.output_dir:
            self.utterance_number += 1
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            saved_path = self.output_dir / "radio-{}-{:04d}.wav".format(
                timestamp, self.utterance_number
            )
            # The STT watcher only sees the final .wav name. Write to a temporary
            # name first, then atomically publish a complete utterance.
            temporary_path = saved_path.with_suffix(".partial")
            with wave.open(str(temporary_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                output.writeframes(self.utterance)
            temporary_path.replace(saved_path)

        message = "AUDIO_CLOSED duration_ms={} reason={}".format(
            round(duration_s * 1000), reason
        )
        if saved_path:
            message += " wav={}".format(saved_path)
        elif duration_s < self.min_utterance_s:
            message += " discarded=too_short"
        else:
            message += " wav=not_saved"
        print(message, flush=True)
        self.active = False
        self.above_threshold_packets = 0
        self.utterance.clear()

    def process(self, pcm, arrived_at):
        rms = pcm_rms(pcm)
        self.pre_roll.append(pcm)
        is_loud = rms >= self.threshold

        if not self.active:
            self.above_threshold_packets = (
                self.above_threshold_packets + 1 if is_loud else 0
            )
            if self.above_threshold_packets >= self.attack_packets:
                self.active = True
                self.started_at = arrived_at - (len(self.pre_roll) - 1) * FRAME_DURATION_S
                self.last_loud_at = arrived_at
                self.utterance = bytearray(b"".join(self.pre_roll))
                print(
                    "AUDIO_OPEN rms={} threshold={} preroll_ms={}".format(
                        rms, self.threshold, round(len(self.pre_roll) * FRAME_DURATION_S * 1000)
                    ),
                    flush=True,
                )
            return rms

        self.utterance.extend(pcm)
        if is_loud:
            self.last_loud_at = arrived_at
        elif arrived_at - self.last_loud_at >= self.hangover_s:
            self._write_utterance(arrived_at, "silence")
        return rms

    def finish(self):
        if self.active:
            self._write_utterance(time.monotonic(), "bridge_stopped")


def main():
    parser = argparse.ArgumentParser(description="Receive BF-777S PCM audio over LAN.")
    parser.add_argument("--bind", default="0.0.0.0", help="local IPv4 address to bind")
    parser.add_argument("--port", type=int, default=7777, help="UDP port (default: 7777)")
    parser.add_argument("--forward-host", metavar="HOST",
                        help="optionally relay validated BF77 packets to this host")
    parser.add_argument("--forward-port", type=int, default=7777,
                        help="UDP port used with --forward-host (default: 7777)")
    parser.add_argument("--no-play", action="store_true", help="receive and report without audio playback")
    parser.add_argument("--play-buffer-ms", type=int, default=600,
                        help="ALSA playback buffer in ms (default: 600; absorbs Wi-Fi jitter)")
    parser.add_argument("--record", metavar="FILE.wav", help="also save every received packet to WAV")
    parser.add_argument("--gate", action="store_true",
                        help="enable speech-energy gate and AUDIO_OPEN/AUDIO_CLOSED events")
    parser.add_argument("--utterance-dir", metavar="DIRECTORY",
                        help="enable the gate and save closed utterances as WAVs in DIRECTORY")
    parser.add_argument("--gate-rms", type=int, default=1200,
                        help="PCM RMS required to open the gate (default: 1200)")
    parser.add_argument("--gate-attack-packets", type=int, default=3,
                        help="consecutive loud 20 ms packets needed to open (default: 3)")
    parser.add_argument("--gate-hangover-ms", type=int, default=3000,
                        help="quiet time before closing an utterance (default: 3000)")
    parser.add_argument("--gate-preroll-ms", type=int, default=250,
                        help="audio preserved before gate opens (default: 250)")
    parser.add_argument("--gate-min-utterance-ms", type=int, default=300,
                        help="discard shorter utterances (default: 300)")
    args = parser.parse_args()

    if args.play_buffer_ms < 20:
        parser.error("--play-buffer-ms must be at least 20")
    if not 1 <= args.forward_port <= 65535:
        parser.error("--forward-port must be between 1 and 65535")
    if args.gate_rms < 1 or args.gate_attack_packets < 1:
        parser.error("--gate-rms and --gate-attack-packets must be positive")
    if args.gate_hangover_ms < 20 or args.gate_preroll_ms < 0 or args.gate_min_utterance_ms < 0:
        parser.error("gate timing values are invalid")
    if args.utterance_dir:
        args.gate = True


    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind((args.bind, args.port))
    forwarder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if args.forward_host else None
    player = open_player(not args.no_play, args.play_buffer_ms)
    recording = open_recording(args.record)
    gate = None
    if args.gate:
        gate = AudioGate(
            args.gate_rms,
            args.gate_attack_packets,
            args.gate_hangover_ms,
            args.gate_preroll_ms,
            args.gate_min_utterance_ms,
            args.utterance_dir,
        )
    print("Listening for 16 kHz mono PCM UDP on {}:{}".format(args.bind, args.port))
    print("Configure this PC's IPv4 address in firmware/secrets.h, then flash the ESP32.")
    if args.forward_host:
        print("Forwarding validated BF77 packets to {}:{}".format(
            args.forward_host, args.forward_port
        ))
    if gate:
        print(
            "Audio gate enabled: rms>={} for {} packets; close after {} ms quiet."
            .format(args.gate_rms, args.gate_attack_packets, args.gate_hangover_ms)
        )
        if args.utterance_dir:
            print("Closed utterances will be saved in {}".format(Path(args.utterance_dir)))

    received = 0
    dropped = 0
    previous_sequence = None
    report_started_at = time.monotonic()
    packets_since_report = 0
    dropped_at_last_report = 0
    previous_arrival_at = None
    maximum_arrival_gap_ms = 0.0
    rms_total = 0
    rms_maximum = 0
    try:
        while True:
            data, sender = receiver.recvfrom(2048)
            if len(data) != PACKET_BYTES:
                continue

            magic, sequence, sample_count = HEADER.unpack_from(data)
            if magic != MAGIC or sample_count != SAMPLES_PER_PACKET:
                continue

            if forwarder is not None:
                forwarder.sendto(data, (args.forward_host, args.forward_port))

            if previous_sequence is not None:
                expected = (previous_sequence + 1) & 0xFFFF
                if sequence != expected:
                    dropped += (sequence - expected) & 0xFFFF
            previous_sequence = sequence

            arrived_at = time.monotonic()
            if previous_arrival_at is not None:
                maximum_arrival_gap_ms = max(
                    maximum_arrival_gap_ms, (arrived_at - previous_arrival_at) * 1000.0
                )
            previous_arrival_at = arrived_at

            pcm = data[HEADER.size:]
            if player is not None:
                player.stdin.write(pcm)
            if recording is not None:
                recording.writeframesraw(pcm)
            rms = gate.process(pcm, arrived_at) if gate else 0

            received += 1
            packets_since_report += 1
            rms_total += rms
            rms_maximum = max(rms_maximum, rms)
            if packets_since_report >= 50:
                elapsed_s = arrived_at - report_started_at
                received_pps = packets_since_report / elapsed_s if elapsed_s else 0.0
                summary = (
                    "packets={} dropped={} dropped_window={} rx_pps={:.1f} "
                    "pcm_hz={:.0f} max_gap_ms={:.1f} source={}:{}"
                ).format(
                    received,
                    dropped,
                    dropped - dropped_at_last_report,
                    received_pps,
                    received_pps * SAMPLES_PER_PACKET,
                    maximum_arrival_gap_ms,
                    sender[0],
                    sender[1],
                )
                if gate:
                    summary += " gate={} rms_avg={} rms_max={}".format(
                        gate.state, rms_total // packets_since_report, rms_maximum
                    )
                print(summary, flush=True)
                report_started_at = arrived_at
                packets_since_report = 0
                dropped_at_last_report = dropped
                maximum_arrival_gap_ms = 0.0
                rms_total = 0
                rms_maximum = 0
    except (BrokenPipeError, KeyboardInterrupt):
        print("\nStopped. packets={} dropped={}".format(received, dropped))
    finally:
        receiver.close()
        if forwarder is not None:
            forwarder.close()
        if gate:
            gate.finish()
        if recording is not None:
            recording.close()
        if player is not None:
            if player.stdin is not None:
                player.stdin.close()
            player.wait()


if __name__ == "__main__":
    main()
