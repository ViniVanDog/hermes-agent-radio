"""Live scanner audio and recording proxy over HTTP."""

from http.server import ThreadingHTTPServer
import ipaddress
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from baofeng_bridge.web import dashboard
from baofeng_bridge.scanner.radio_scanner import Scanner, handler_for
from baofeng_bridge.scanner.scanner_audio import FRAME_BYTES, HEADER, VOICE


class ProxyTests(unittest.TestCase):
    def test_metadata_and_wav_pass_through_vm_without_vm_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / 'host'
            vm = root / 'vm'
            scanner = Scanner(host, 'never-opened', vad_factory=lambda: None)
            scanner.update(profile='scanner', phase='listening')
            scanner.audio.configure(True, 1024 * 1024, 1)
            scanner.audio.set_active(True)
            scanner.audio.transition(462625000, False)
            control = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(scanner, {ipaddress.ip_address('127.0.0.1')}))
            proxy = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.handler_for(
                dashboard.EventStore(vm / 'events.jsonl'), 'http://127.0.0.1:{}'.format(control.server_port)))
            for server in (control, proxy):
                threading.Thread(target=server.serve_forever, daemon=True).start()
            base = 'http://127.0.0.1:{}'.format(proxy.server_port)
            stream = None
            try:
                stream = urlopen(base + '/api/scanner/audio', timeout=3)
                pcm = b'\x00\x08' * 320
                scanner.audio.publish(pcm, True, now=1234)
                packet = stream.read(FRAME_BYTES)
                if packet[HEADER.size:] != pcm:
                    packet = stream.read(FRAME_BYTES)
                self.assertEqual(HEADER.unpack_from(packet), (1234, 462625000, VOICE))
                self.assertEqual(packet[HEADER.size:], pcm)
                scanner.audio.finish_visit()
                record = scanner.audio.recordings()[0]
                with urlopen(base + '/api/scanner/recordings/' + record['filename'], timeout=3) as response:
                    data = response.read()
                self.assertEqual(data[:4], b'RIFF')
                self.assertEqual(data, scanner.audio.recording_path(record['filename']).read_bytes())
                self.assertEqual(list(vm.rglob('*.wav')), [])
                self.assertEqual(list(vm.iterdir()), [vm / 'events.jsonl'])
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + '/api/scanner/recordings/%2e%2e%2fhermes.bin', timeout=3)
                self.assertEqual(error.exception.code, 404)
            finally:
                scanner.audio.set_active(False)
                if stream:
                    stream.close()
                for server in (proxy, control):
                    server.shutdown()
                    server.server_close()
