import ast
import importlib.util
import logging
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from radio_bridge.radio_input import radio_prompt_reason

from tests.support import ROOT
# A Hermes Agent checkout to test the patches against (unmodified, or only base-patched).
HERMES_SRC = Path(os.environ.get('HERMES_AGENT_SRC', '~/.hermes/hermes-agent')).expanduser()
UPSTREAM_VOICE = HERMES_SRC / 'hermes_cli/cli_voice_mixin.py'


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'hermes_patches' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = _load('radio_input_patch', 'apply_radio_input_filter.py')
BASE = _load('radio_base_patch', 'apply_radio_base.py')


def base_patched_voice_source():
    text = UPSTREAM_VOICE.read_text()
    if BASE.MARK not in text:
        for anchor, replacement in BASE.VOICE_EDITS:
            text = text.replace(anchor, replacement)
    return text


SNAPSHOT_USABLE = UPSTREAM_VOICE.exists() and INSTALLER.MARK not in UPSTREAM_VOICE.read_text()


@unittest.skipUnless(SNAPSHOT_USABLE, 'set HERMES_AGENT_SRC to an unpatched Hermes Agent checkout')
class VoiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        path = root / 'hermes_cli/cli_voice_mixin.py'
        path.parent.mkdir()
        path.write_text(base_patched_voice_source())
        self.assertTrue(INSTALLER.patch(root))
        self.assertFalse(INSTALLER.patch(root))
        self.source = path.read_text()
        self.root = root

    def tearDown(self):
        self.temporary.cleanup()

    def execute_voice_path(self, method, text, gated=True):
        tree = ast.parse(self.source)
        original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CLIVoiceMixin')
        methods = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in
                   ('_voice_stop_and_transcribe', '_voice_submit_barge_utterance')]
        fixture = ast.Module(body=[ast.ClassDef(name='VoiceFixture', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
        ast.fix_missing_locations(fixture)
        events = []
        namespace = {'os': os, 't': lambda key, **kwargs: key,
                     '_radio_dashboard_event': lambda kind, value: events.append((kind, value)),
                     '_unlink_quietly': lambda path: Path(path).unlink(missing_ok=True)}
        exec(compile(fixture, '<installed-voice-paths>', 'exec'), namespace)
        voice = namespace['VoiceFixture']()
        voice._voice_lock = threading.Lock()
        voice._voice_recording = True
        voice._voice_processing = False
        voice._voice_mode = True
        voice._voice_continuous = False
        voice._voice_stt_model = lambda: 'fake-stt'
        voice._voice_stt_provider = lambda: 'fake'
        voice._voice_beep = lambda **kwargs: None
        voice._voice_invalidate = lambda: None
        voice._attached_images = []
        voice._pending_input = queue.Queue()
        voice._voice_barge_capture = SimpleNamespace(clear=lambda: None)
        voice._voice_barge_phase = 'agent'
        voice._no_speech_count = 0
        wav = self.root / 'recording.wav'
        wav.write_bytes(b'fixture; never sent to an API')
        voice._voice_recorder = SimpleNamespace(stop=lambda: str(wav))
        cli = ModuleType('cli')
        cli._DIM = cli._RST = ''
        cli._VoiceInputMessage = str
        cli._cprint = lambda *args, **kwargs: None
        cli.logger = logging.getLogger('test-radio-input')
        tools = ModuleType('tools')
        stop = ModuleType('tools.voice_mode_transcript')
        stop.is_voice_stop_phrase = lambda value: False
        mode = ModuleType('tools.voice_mode')
        mode.transcribe_recording = Mock(return_value={'success': True, 'transcript': text})
        modules = {'cli': cli, 'tools': tools, 'tools.voice_mode_transcript': stop, 'tools.voice_mode': mode,
                   'hermes_cli': ModuleType('hermes_cli'),
                   'hermes_cli.radio_input_filter': sys.modules['radio_bridge.radio_input']}
        environment = {'HERMES_RADIO_GATED': '1'} if gated else {}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, environment, clear=True):
            call = getattr(voice, method)
            call(str(wav)) if method == '_voice_submit_barge_utterance' else call()
        self.assertEqual(mode.transcribe_recording.call_count, 1)
        return voice, events

    def test_both_paths_record_stt_but_never_queue_radio_prompts(self):
        for method in ('_voice_stop_and_transcribe', '_voice_submit_barge_utterance'):
            for text in ('Welcome one.', 'Battery low.', 'Channel sixteen'):
                with self.subTest(method=method, text=text):
                    voice, events = self.execute_voice_path(method, text)
                    self.assertTrue(voice._pending_input.empty())
                    self.assertEqual(events.count(('stt', text)), 1)
                    self.assertEqual(events.count(('radio_prompt_ignored', text)), 1)
                    self.assertFalse(voice._voice_processing)
                    self.assertEqual(voice._no_speech_count, 0)

    def test_real_speech_reaches_queue_unchanged(self):
        for method in ('_voice_stop_and_transcribe', '_voice_submit_barge_utterance'):
            for text in ('sim', 'Testando, testando', 'O rádio disse battery low, o que faço?'):
                with self.subTest(method=method, text=text):
                    voice, events = self.execute_voice_path(method, text)
                    self.assertEqual(voice._pending_input.get_nowait(), text)
                    self.assertFalse(any(kind == 'radio_prompt_ignored' for kind, _ in events))

    def test_regular_nonradio_voice_mode_is_not_filtered(self):
        for method in ('_voice_stop_and_transcribe', '_voice_submit_barge_utterance'):
            voice, events = self.execute_voice_path(method, 'Welcome one.', gated=False)
            self.assertEqual(voice._pending_input.get_nowait(), 'Welcome one.')
            self.assertFalse(any(kind == 'radio_prompt_ignored' for kind, _ in events))
