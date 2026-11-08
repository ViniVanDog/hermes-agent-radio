import importlib.util
import os
from tests.support import ROOT
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("radio_speech", ROOT / "hermes_patches/radio_speech.py")
speech = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(speech)


class SpeechTests(unittest.TestCase):
    def test_language_choice(self):
        with patch.dict(os.environ, {"BAOFENG_LANG": ""}):
            self.assertEqual(speech.language("pt-BR-FranciscaNeural"), "pt")
            self.assertEqual(speech.language("en-US-AriaNeural"), "en")
            self.assertEqual(speech.language(), "en")
        with patch.dict(os.environ, {"BAOFENG_LANG": "pt-BR"}):
            self.assertEqual(speech.language("en-US-AriaNeural"), "pt")

    def test_english_rules(self):
        self.assertEqual(speech.normalize("Back at 14:00, 9:05 or 14:30 & 2x labels, 24x14cm.", "en"),
                         "Back at 14 o'clock, 9 oh 5 or 14 30 and 2 units of labels, 24 by 14 centimetres.")
        self.assertEqual(speech.normalize("Item(s) sent; 1 copy(s) left (approx)", "en"),
                         "Items sent. 1 copy left, approx,")

    def test_portuguese_rules(self):
        self.assertEqual(speech.normalize("Volto às 14:00 ou 14:30 & 2x etiquetas, 24x14cm.", "pt"),
                         "Volto às 14 horas ou 14 e 30 e 2 unidades de etiquetas, 24 por 14 centímetros.")

    def test_abbreviations_do_not_split_sentences(self):
        self.assertEqual([s for s, _ in speech.segments("Call Mr. Smith. Done.", "en")],
                         ["Call Mr. Smith.", "Done."])
        self.assertEqual([s for s, _ in speech.segments("Ligue para o Sr. Silva. Pronto.", "pt")],
                         ["Ligue para o Sr. Silva.", "Pronto."])

    def test_rule_tables_cover_both_languages(self):
        for table in (speech.WORDS, speech.ABBREVIATIONS, speech.SAY):
            self.assertEqual(set(table), {"en", "pt"})
        self.assertEqual(set(speech.SAY["en"]), set(speech.SAY["pt"]))


if __name__ == "__main__":
    unittest.main()
