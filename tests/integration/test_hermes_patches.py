"""Apply every hermes_patches/apply_radio_*.py, in order, to a temporary copy of a Hermes checkout.

Point HERMES_AGENT_SRC at an *unpatched* Hermes Agent checkout (a fresh
`git clone`, or ~/.hermes/hermes-agent before patching); skipped otherwise.
Run it after every `hermes update` before re-applying the patches for real.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from tests.support import ROOT
HERMES = Path(os.environ.get('HERMES_AGENT_SRC', '~/.hermes/hermes-agent')).expanduser()
FILES = ('hermes_cli/cli_voice_mixin.py', 'hermes_cli/cli_tui_runtime_mixin.py', 'tools/tts_tool_providers.py')
ORDER = ('base', 'gate', 'input_filter', 'speech', 'announce', 'context')


def _usable():
    if not all((HERMES / name).exists() for name in FILES):
        return False
    return 'baofeng-radio' not in (HERMES / FILES[0]).read_text()


USABLE = _usable()


@unittest.skipUnless(USABLE, 'set HERMES_AGENT_SRC to an unpatched Hermes Agent checkout')
class PatchChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'hermes'
        for name in FILES:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(HERMES / name, self.root / name)
        self.env = dict(os.environ, HOME=str(Path(self.tmp.name) / 'home'))

    def tearDown(self):
        self.tmp.cleanup()

    def apply_all(self):
        for name in ORDER:
            result = subprocess.run([sys.executable, str(ROOT / 'hermes_patches' / f'apply_radio_{name}.py'),
                                     str(self.root)], env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f'{name}: {result.stdout}{result.stderr}')
            yield name, result.stdout

    def test_chain_applies_compiles_and_is_idempotent(self):
        list(self.apply_all())
        first = {name: (self.root / name).read_text() for name in FILES}
        for name, text in first.items():
            compile(text, name, 'exec')
        list(self.apply_all())
        self.assertEqual(first, {name: (self.root / name).read_text() for name in FILES})
        voice, runtime = first[FILES[0]], first[FILES[1]]
        self.assertEqual(voice.count('_radio_dashboard_event("stt", transcript)'), 2)  # normal + barge-in
        self.assertEqual(runtime.count("_signal.signal(_signal.SIGUSR1, self._tui_queue_radio_context_reset)"), 1)
        self.assertEqual(runtime.count('name="radio-gate"'), 1)
        self.assertEqual(runtime.count('name="radio-announce"'), 1)
        self.assertEqual(runtime.count('self._radio_maybe_rollover()'), 2)

    def test_event_logger_writes_only_in_radio_mode(self):
        list(self.apply_all())
        tree = ast.parse((self.root / FILES[0]).read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_radio_dashboard_event')
        namespace = {'os': os, 'json': json, 'time': __import__('time')}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'event', 'exec'), namespace)
        log = Path(self.tmp.name) / 'events.jsonl'
        with unittest.mock.patch.dict(os.environ, {'BAOFENG_EVENTS_FILE': str(log)}, clear=False):
            os.environ.pop('HERMES_RADIO_GATED', None)
            namespace['_radio_dashboard_event']('stt', 'ignored')
            self.assertFalse(log.exists())
            os.environ['HERMES_RADIO_GATED'] = '1'
            try:
                namespace['_radio_dashboard_event']('stt', 'hello')
            finally:
                os.environ.pop('HERMES_RADIO_GATED')
        record = json.loads(log.read_text())
        self.assertEqual((record['kind'], record['text']), ('stt', 'hello'))


if __name__ == '__main__':
    unittest.main()
