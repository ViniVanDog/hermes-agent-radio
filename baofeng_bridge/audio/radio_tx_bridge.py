#!/usr/bin/env python3
"""Send Hermes TTS from ALSA loopback to the ESP32 BFTX endpoint."""

import argparse
from collections import deque
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import threading
import time

SAMPLE_RATE = 16000
SAMPLES_PER_PACKET = 320
FRAME_BYTES = SAMPLES_PER_PACKET * 2
FRAME_SECONDS = SAMPLES_PER_PACKET / SAMPLE_RATE
SAMPLES = struct.Struct("<320h")
HEADER = struct.Struct("<IBBHH")
MAGIC = 0x58544642  # BFTX
TX_BEGIN, TX_AUDIO, TX_END = 1, 2, 3
PTT_LEAD_SECONDS = 1.5
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


def rms(pcm):
    values = SAMPLES.unpack(pcm)
    return math.isqrt(sum(value * value for value in values) // len(values))


def read_frame(stream):
    """Read exactly one PCM packet from a pipe, or return None at end of stream."""
    chunks = bytearray()
    while len(chunks) < FRAME_BYTES:
        part = stream.read(FRAME_BYTES - len(chunks))
        if not part:
            return None
        chunks.extend(part)
    return bytes(chunks)


class Bftx:
    """One radio session fed from the live TTS capture, sent by its own paced thread.

    The C3's lwIP UDP mailbox holds only ~6 datagrams, so packets must leave one
    per 20 ms like tx.py does. Bursts (the old 16-frame startup burst, or catch-up
    after a late arecord chunk) overflowed it: lost packets and DAC underruns.
    The ESP32 builds its own 6-frame jitter buffer, so no startup burst is needed.
    """

    MAX_CATCH_UP_FRAMES = 2   # never more than this back to back
    IDLE_POLL_S = 0.005

    def __init__(self, host, port, begin_repeats, startup_buffer_ms, clock=time.monotonic,
                 start_thread=True):
        self.destination = (host, port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sequence = 0
        self.begin_repeats = begin_repeats
        self.startup_buffer_frames = max(1, math.ceil(startup_buffer_ms / 1000 / FRAME_SECONDS))
        self.pending = deque()
        self.active = False
        self.input_closed = False
        self.playback_started = False
        self.start_at = self.next_frame_at = 0.0
        self.clock = clock
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="bftx-sender", daemon=True)
        if start_thread:
            self.thread.start()

    def send(self, packet_type, payload=b"", sequence=None):
        sequence = self.sequence if sequence is None else sequence
        self.socket.sendto(HEADER.pack(MAGIC, packet_type, 0, sequence, len(payload)) + payload,
                           self.destination)
        if packet_type != TX_BEGIN or sequence == self.sequence:
            self.sequence = (self.sequence + 1) & 0xFFFF

    def begin(self, now):
        with self.lock:
            if self.active:
                self.input_closed = False
                return
            first_sequence = self.sequence
            self.send(TX_BEGIN)
            for _ in range(self.begin_repeats - 1):
                self.send(TX_BEGIN, sequence=first_sequence)
            self.active = True
            self.input_closed = False
            self.playback_started = False
            self.start_at = now + PTT_LEAD_SECONDS
        emit_event("tx_begin")

    def reopen_input(self):
        with self.lock:
            self.input_closed = False

    def enqueue(self, pcm):
        with self.lock:
            for offset in range(0, len(pcm), FRAME_BYTES):
                self.pending.append(pcm[offset:offset + FRAME_BYTES].ljust(FRAME_BYTES, bytes(1)))

    def close_input(self):
        with self.lock:
            self.input_closed = True

    def step(self, now):
        """Send at most what is due now; returns seconds until the next check."""
        events = []
        with self.lock:
            delay = self._step_locked(now, events)
        for kind, fields in events:
            emit_event(kind, **fields)
        return delay

    def _step_locked(self, now, events):
        if not self.active:
            return self.IDLE_POLL_S
        if not self.playback_started:
            # Wait out the PTT lead, then keep a small local buffer against capture jitter.
            ready = len(self.pending) >= self.startup_buffer_frames or (
                self.input_closed and self.pending)
            if now < self.start_at or not ready:
                if self.input_closed and not self.pending and now >= self.start_at:
                    self._finish(events)
                return self.IDLE_POLL_S
            self.playback_started = True
            self.next_frame_at = now
            print("RADIO_TX_PLAYBACK_STARTED buffered_frames={}".format(len(self.pending)),
                  flush=True)
            events.append(("tx_playback", {"buffered_frames": len(self.pending)}))

        if now < self.next_frame_at:
            return self.next_frame_at - now
        if not self.pending:
            if self.input_closed:
                self._finish(events)
                return self.IDLE_POLL_S
            self.next_frame_at = now   # capture is late: wait for it, never burst afterwards
            return self.IDLE_POLL_S
        # Late wake-ups catch up by at most MAX_CATCH_UP_FRAMES; the rest of the lag is dropped
        # from the schedule (a short gap) instead of becoming a burst the ESP32 cannot absorb.
        self.next_frame_at = max(self.next_frame_at, now - (self.MAX_CATCH_UP_FRAMES - 1) * FRAME_SECONDS)
        self.send(TX_AUDIO, self.pending.popleft())
        self.next_frame_at += FRAME_SECONDS
        return max(0.0, self.next_frame_at - now)

    def _finish(self, events):
        self.send(TX_END)
        self.active = False
        self.input_closed = False
        self.playback_started = False
        print("RADIO_TX_COMPLETE", flush=True)
        events.append(("tx_complete", {}))

    def _run(self):
        while not self.stopped.is_set():
            delay = self.step(self.clock())
            if delay > 0:
                self.stopped.wait(min(delay, 0.05))

    def close(self):
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        self.socket.close()


class Gate:
    def __init__(self, threshold, attack_packets, hangover_ms, preroll_ms, maximum_ms):
        self.threshold = threshold
        self.attack_packets = attack_packets
        self.hangover = hangover_ms / 1000.0
        self.maximum = maximum_ms / 1000.0
        self.pre_roll = deque(maxlen=max(1, round(preroll_ms / 1000.0 / FRAME_SECONDS)))
        self.active = False
        self.above = 0
        self.last_loud = self.started = 0.0
        self.frames = 0

    def process(self, pcm, now):
        level = rms(pcm)
        loud = level >= self.threshold
        self.pre_roll.append(pcm)
        if not self.active:
            self.above = self.above + 1 if loud else 0
            if self.above < self.attack_packets:
                return b""
            self.active = True
            self.started = now
            self.last_loud = now
            output = b"".join(self.pre_roll)
            self.frames = len(output) // FRAME_BYTES
            print("TTS_AUDIO_OPEN rms={}".format(level), flush=True)
            emit_event("tts_audio_open", rms=level)
            return output

        self.frames += 1
        if loud:
            self.last_loud = now
        if now - self.started >= self.maximum:
            self.close("maximum duration")
        elif not loud and now - self.last_loud >= self.hangover:
            self.close("silence")
        return pcm

    def close(self, reason):
        self.active = False
        self.above = 0
        print("TTS_AUDIO_CLOSED duration_ms={} reason={}".format(
            round(self.frames * FRAME_SECONDS * 1000), reason), flush=True)
        emit_event("tts_audio_closed", duration_ms=round(self.frames * FRAME_SECONDS * 1000),
                   reason=reason)


def main():
    parser = argparse.ArgumentParser(description="Transmit gated Hermes loopback audio over Baofeng.")
    parser.add_argument("--esp32-host", required=True)
    parser.add_argument("--tx-port", type=int, default=7778)
    parser.add_argument("--capture-device", default="plughw:HermesLoopback,0,0")
    parser.add_argument("--gate-rms", type=int, default=180)
    parser.add_argument("--gate-attack-packets", type=int, default=2)
    parser.add_argument("--gate-hangover-ms", type=int, default=3000)
    parser.add_argument("--gate-preroll-ms", type=int, default=80)
    parser.add_argument("--max-utterance-ms", type=int, default=55000)
    parser.add_argument("--begin-repeats", type=int, default=3,
                        help="TX_BEGIN copies (same sequence) so one lost datagram does not lose PTT")
    parser.add_argument("--startup-buffer-ms", type=int, default=320,
                        help="local buffer before paced sending starts (absorbs capture jitter)")
    parser.add_argument("--speech-lead-ms", type=int, default=300,
                        help="silence before speech so receiving radios open cleanly")
    args = parser.parse_args()
    if not 1 <= args.tx_port <= 65535:
        parser.error("invalid TX port")
    if min(args.gate_rms, args.gate_attack_packets, args.gate_hangover_ms,
           args.max_utterance_ms, args.begin_repeats, args.startup_buffer_ms) < 1 or (
            args.gate_preroll_ms < 0 or args.speech_lead_ms < 0 or args.startup_buffer_ms > 480):
        parser.error("invalid gate setting")

    arecord = shutil.which("arecord")
    if not arecord:
        raise SystemExit("arecord is required; install alsa-utils")
    gate = Gate(args.gate_rms, args.gate_attack_packets, args.gate_hangover_ms,
                args.gate_preroll_ms, args.max_utterance_ms)
    sender = Bftx(args.esp32_host, args.tx_port, args.begin_repeats,
                  args.startup_buffer_ms)
    speech_lead = bytes(FRAME_BYTES) * math.ceil(args.speech_lead_ms / 1000 / FRAME_SECONDS)
    capture = None
    print("Capturing {} -> BFTX {}:{}".format(
        args.capture_device, args.esp32_host, args.tx_port), flush=True)
    try:
        while True:
            capture = subprocess.Popen(
                [arecord, "-q", "-D", args.capture_device, "-f", "S16_LE", "-r", "16000",
                 "-c", "1", "-t", "raw"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            assert capture.stdout is not None
            while True:
                pcm = read_frame(capture.stdout)
                if pcm is None:
                    break
                now = time.monotonic()
                output = gate.process(pcm, now)
                if output:
                    if not sender.active:
                        sender.begin(now)
                        sender.enqueue(speech_lead)
                        print("RADIO_TX_BEGIN streaming", flush=True)
                    else:
                        sender.reopen_input()
                    sender.enqueue(output)
                if sender.active and not gate.active:
                    sender.close_input()
            capture.wait()
            capture = None
            time.sleep(0.15)
    except KeyboardInterrupt:
        print("Stopped.", flush=True)
    finally:
        sender.close()
        if capture is not None:
            capture.terminate()
            try:
                capture.wait(timeout=3)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait()


if __name__ == "__main__":
    main()
