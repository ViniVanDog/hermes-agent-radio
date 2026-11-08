import importlib.util
import os
from pathlib import Path
from tests.support import ROOT
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from radio_bridge.radio_context import handoff, maybe_rollover, HANDOFF_PREFIX


class ContextTests(unittest.TestCase):
    def cli(self, tokens=20000):
        obj = SimpleNamespace(_agent_running=False, agent=SimpleNamespace(
            context_compressor=SimpleNamespace(last_prompt_tokens=tokens)),
            _last_turn_result={'last_prompt_tokens': tokens},
            conversation_history=[{'role': 'user', 'content': 'turn on the porch light?'},
                                  {'role': 'assistant', 'content': 'Should I turn it on now?'}],
            resets=0, pending=['sim'])
        def reset(silent):
            obj.resets += 1
            obj.conversation_history = []
        obj.new_session = reset
        return obj

    @patch.dict(os.environ, {'HERMES_RADIO_GATED': '1'})
    def test_boundary_and_confirmation_survive_once(self):
        cli = self.cli()
        self.assertTrue(maybe_rollover(cli))
        self.assertEqual(cli.pending, ['sim'])
        self.assertIn('Should I turn it on now?', cli.conversation_history[0]['content'])
        self.assertFalse(maybe_rollover(cli))
        self.assertEqual(cli.resets, 1)

    @patch.dict(os.environ, {'HERMES_RADIO_GATED': '1'})
    def test_no_reset_during_task_or_below_limit(self):
        cli = self.cli(19999)
        self.assertFalse(maybe_rollover(cli))
        cli._last_turn_result['last_prompt_tokens'] = 40000
        cli._agent_running = True
        self.assertFalse(maybe_rollover(cli))
        self.assertEqual(cli.resets, 0)

    @patch.dict(os.environ, {'HERMES_RADIO_GATED': '1', 'HERMES_RADIO_CONTEXT_MAX_TOKENS': '0'})
    def test_disabled(self):
        self.assertFalse(maybe_rollover(self.cli()))

    def test_handoff_is_bounded_and_keeps_exact_candidate_paths(self):
        history = [{'role': 'user', 'content': HANDOFF_PREFIX + 'old'},
                   {'role': 'user', 'content': 'open the report for Ana'},
                   {'role': 'tool', 'content': 'x' * 20000},
                   {'role': 'tool', 'content': 'candidate: reports/Ana 2026.pdf'},
                   {'role': 'assistant', 'content': 'Which file?'}]
        text = handoff(history)[0]['content']
        self.assertLess(len(text), 8500)
        self.assertIn('reports/Ana 2026.pdf', text)
        self.assertNotIn('old', text)
        self.assertNotIn('x' * 100, text)
        self.assertEqual(handoff(handoff(history)), [])

    def test_patch_compiles_and_is_idempotent_on_installed_runtime(self):
        hermes = Path(os.environ.get('HERMES_AGENT_SRC', '~/.hermes/hermes-agent')).expanduser()
        source = hermes / 'hermes_cli/cli_tui_runtime_mixin.py'
        if not source.exists():
            self.skipTest('set HERMES_AGENT_SRC to a Hermes Agent checkout')
        spec = importlib.util.spec_from_file_location('context_patch', ROOT /
                                                      'hermes_patches/apply_radio_context.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'hermes_cli/cli_tui_runtime_mixin.py'
            target.parent.mkdir()
            target.write_text(source.read_text())
            self.assertEqual(mod.patch(tmp), mod.MARK not in source.read_text())
            self.assertFalse(mod.patch(tmp))
            text = target.read_text()
            self.assertEqual(text.count('self._radio_maybe_rollover()'), 2)

    def test_handoff_note_follows_baofeng_lang(self):
        history = self.cli().conversation_history
        with patch.dict(os.environ, {'BAOFENG_LANG': 'pt-BR'}):
            text = handoff(history)[0]['content']
        self.assertTrue(text.startswith(HANDOFF_PREFIX + '\nRegistro da última troca'))
        with patch.dict(os.environ, {'BAOFENG_LANG': 'en'}):
            self.assertIn('Record of the last exchange', handoff(history)[0]['content'])
