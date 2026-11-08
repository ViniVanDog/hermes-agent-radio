import os
import unittest
from unittest.mock import patch

from radio_bridge.radio_input import radio_prompt_reason


class PromptTests(unittest.TestCase):
    def test_recognizes_complete_radio_announcements(self):
        for text in ('Welcome one.', 'WELCOME 1!', 'Welcome channel sixteen',
                     'Battery low.', 'Low battery!', 'Battery is low', 'Low voltage',
                     'Please charge the battery', 'Bateria fraca.', 'Bateria baixa',
                     'Recarregue a bateria', 'Canal três', 'Channel 16', 'Scan on',
                     'VOX off', 'High power', 'Power on', 'Welcome one. Battery low.'):
            with self.subTest(text=text):
                self.assertTrue(radio_prompt_reason(text))

    def test_preserves_real_requests_and_short_confirmations(self):
        for text in ('sim', 'não', 'um', 'one', '1', 'Testando, testando',
                     'Hermes, tá me ouvindo?', 'O rádio disse battery low, o que faço?',
                     'Battery low, o que eu faço?', 'Repita Welcome one em português',
                     'Welcome one. Hermes, confirma se está ouvindo.',
                     'A bateria está fraca, devo trocar?', 'Canal 3 está ocupado',
                     'Welcome to the shop', '', '!!!', None):
            with self.subTest(text=text):
                self.assertIsNone(radio_prompt_reason(text))
