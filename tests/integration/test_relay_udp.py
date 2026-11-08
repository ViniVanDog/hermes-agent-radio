"""UDP relay forwarding and scanner isolation through a real subprocess."""

from pathlib import Path
import tempfile
import socket
import struct
import subprocess
import sys
import time
import unittest

from tests.support import ROOT


class RelayTests(unittest.TestCase):
    def test_udp_audio_reaches_detector_and_only_hermes_in_hermes_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            state.write_text('{"profile":"hermes"}')
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as hermes, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as detector, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                hermes.bind(('127.0.0.1', 0))
                detector.bind(('127.0.0.1', 0))
                hermes.settimeout(.3)
                detector.settimeout(1)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
                    reserve.bind(('127.0.0.1', 0))
                    port = reserve.getsockname()[1]
                process = subprocess.Popen([sys.executable, '-m', 'baofeng_bridge.audio.udp_relay', '--bind', '127.0.0.1',
                    '--port', str(port), '--target-host', '127.0.0.1', '--target-port', str(hermes.getsockname()[1]),
                    '--scanner-audio-port', str(detector.getsockname()[1]), '--scanner-state-file', str(state)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=ROOT)
                try:
                    time.sleep(.15)
                    packet = struct.pack('<IHH', 0x37374642, 1, 320) + bytes(640)
                    sender.sendto(packet, ('127.0.0.1', port))
                    self.assertEqual(detector.recv(1000), packet)
                    self.assertEqual(hermes.recv(1000), packet)
                    state.write_text('{"profile":"scanner"}')
                    time.sleep(.12)
                    sender.sendto(packet, ('127.0.0.1', port))
                    self.assertEqual(detector.recv(1000), packet)
                    with self.assertRaises(socket.timeout):
                        hermes.recv(1000)
                    state.write_text('{"profile":"hermes"}')
                    time.sleep(.12)
                    sender.sendto(packet, ('127.0.0.1', port))
                    self.assertEqual(hermes.recv(1000), packet)
                finally:
                    process.terminate()
                    process.communicate(timeout=3)
