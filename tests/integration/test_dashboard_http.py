"""Dashboard HTTP boundaries, static assets and scanner proxy requests."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from baofeng_bridge.web import dashboard


class DashboardTests(unittest.TestCase):
    def test_scanner_tab_and_proxy_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = dashboard.EventStore(Path(directory) / 'events.jsonl')
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.handler_for(store))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            root = 'http://127.0.0.1:{}'.format(server.server_port)
            try:
                html = urlopen(root).read().decode()
                self.assertIn('id="tab-scanner"', html)
                self.assertIn('id="scan-grace"', html)
                self.assertIn('channel 1', html)
                with urlopen(root + '/scanner-audio.js') as response:
                    self.assertEqual(response.read(), (dashboard.PROJECT_ROOT /
                        'baofeng_bridge/web/scanner_audio_web.js').read_bytes())
                with patch.object(dashboard, 'scanner_request', return_value=(200, {'profile': 'hermes'})) as proxy:
                    self.assertEqual(json.load(urlopen(root + '/api/scanner'))['profile'], 'hermes')
                    proxy.assert_called_once_with('http://127.0.0.1:8788', 'status')
                request = Request(root + '/api/scanner/start', data=b'{}',
                                  headers={'Origin': 'http://unrelated.example'}, method='POST')
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 403)
                request = Request(root + '/api/scanner/start', data=b'[]', method='POST')
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
