#!/usr/bin/env python3
"""Forward validated Baofeng RX packets from the ESP32 LAN host to a VM."""

import argparse
import json
import os
from pathlib import Path
import socket
import struct
import time

from baofeng_bridge.paths import PROJECT_ROOT

MAGIC = 0x37374642  # BF77
HEADER = struct.Struct("<IHH")
SAMPLES_PER_PACKET = 320
PACKET_BYTES = HEADER.size + SAMPLES_PER_PACKET * 2


class ScannerRoute:
    """Keep scanner audio out of Hermes, including after a scanner crash."""
    def __init__(self, state_file, clock=time.monotonic):
        self.path = Path(state_file)
        self.clock = clock
        self.checked_at = float('-inf')
        self.scanner = False
        self.stamp = None

    def active(self):
        now = self.clock()
        if now - self.checked_at >= .1:
            self.checked_at = now
            try:
                info = self.path.stat()
                stamp = (info.st_mtime_ns, info.st_size)
                if stamp == self.stamp:
                    return self.scanner
                self.scanner = json.loads(self.path.read_text()).get('profile') != 'hermes'
                self.stamp = stamp
            except FileNotFoundError:
                # Keep a previous scanner/recovery decision if its state
                # disappears; only a confirmed Hermes state resumes forwarding.
                pass
            except (OSError, ValueError):
                # An unreadable existing state must not feed scanner audio to AI.
                self.scanner = True
        return self.scanner


def main():
    parser = argparse.ArgumentParser(description="Forward Baofeng UDP packets to a VM.")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, default=7777)
    parser.add_argument("--allow-source", help="optional ESP32 IPv4 address")
    parser.add_argument('--scanner-audio-port', type=int, default=7781)
    parser.add_argument('--scanner-state-file', default=os.environ.get(
        'BAOFENG_SCANNER_STATE', str(PROJECT_ROOT / 'diagnostics/scanner/state.json')))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.target_port <= 65535:
        parser.error("ports must be in 1..65535")

    inbound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inbound.bind((args.bind, args.port))
    outbound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    destination = (args.target_host, args.target_port)
    scanner_destination = ('127.0.0.1', args.scanner_audio_port)
    scanner_route = ScannerRoute(args.scanner_state_file)
    forwarded = rejected = 0
    last_report = time.monotonic()
    print("Relaying BF77 UDP {}:{} -> {}:{}".format(args.bind, args.port, *destination), flush=True)
    try:
        while True:
            packet, source = inbound.recvfrom(PACKET_BYTES + 1)
            valid = len(packet) == PACKET_BYTES
            if valid:
                magic, _sequence, sample_count = HEADER.unpack_from(packet)
                valid = magic == MAGIC and sample_count == SAMPLES_PER_PACKET
            if valid and args.allow_source:
                valid = source[0] == args.allow_source
            if not valid:
                rejected += 1
                continue
            # Mirror for local VAD. The original receiver receives nothing
            # while scanner profile (or recovery) owns the radio.
            outbound.sendto(packet, scanner_destination)
            if not scanner_route.active():
                outbound.sendto(packet, destination)
            forwarded += 1
            now = time.monotonic()
            if now - last_report >= 5:
                print("relay packets={} rejected={}".format(forwarded, rejected), flush=True)
                last_report = now
    except KeyboardInterrupt:
        print("Stopped. forwarded={} rejected={}".format(forwarded, rejected), flush=True)
    finally:
        inbound.close()
        outbound.close()


if __name__ == "__main__":
    main()
