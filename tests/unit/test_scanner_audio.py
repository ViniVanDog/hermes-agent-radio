"""Scanner audio framing, WAV recording and configuration unit tests."""

from pathlib import Path
import tempfile
import unittest
import wave

from baofeng_bridge.scanner.radio_scanner import validate_config
from baofeng_bridge.scanner.scanner_audio import ScannerAudio, FRAME_BYTES, HEADER, PCM_BYTES, TUNING, VOICE, NO_DATA


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audio = ScannerAudio(self.root / 'host/recordings')

    def tearDown(self):
        self.audio.set_active(False)
        self.tmp.cleanup()

    def test_frames_carry_frequency_timestamp_and_tuning_state(self):
        self.audio.set_active(True)
        self.audio.transition(462625000, False)
        listener = self.audio.subscribe()
        pcm = b'\x01\x00' * 320
        self.audio.publish(pcm, True, now=1234.5)
        packet = listener.get_nowait()
        self.assertEqual(len(packet), FRAME_BYTES)
        self.assertEqual(HEADER.unpack_from(packet), (1234.5, 462625000, VOICE))
        self.assertEqual(packet[HEADER.size:], pcm)
        self.audio.transition(467637500, True)
        _, frequency, flags = HEADER.unpack_from(self.audio.heartbeat())
        self.assertEqual(frequency, 467637500)
        self.assertTrue(flags & TUNING and flags & NO_DATA)

    def test_slow_browser_queue_is_bounded_and_disconnected(self):
        self.audio.set_active(True)
        listener = self.audio.subscribe()
        for index in range(100):
            self.audio.publish(bytes(PCM_BYTES), False, now=index)
        self.assertEqual(listener.qsize(), 50)
        self.assertEqual(HEADER.unpack_from(listener.get_nowait())[0], 50)
        self.audio.set_active(False)
        packets = []
        while not listener.empty():
            packets.append(listener.get_nowait())
        self.assertIsNone(packets[-1])

    def test_audio_is_not_recorded_or_streamed_in_inactive_profile(self):
        self.audio.configure(True, 1024 * 1024, .1)
        self.audio.publish(bytes(PCM_BYTES), True)
        self.assertEqual(list(self.audio.directory.iterdir()), [])
        with self.assertRaises(RuntimeError):
            self.audio.subscribe()

    def test_voice_clips_have_preroll_tail_frequency_and_valid_wav(self):
        self.audio.configure(True, 1024 * 1024, .06)
        self.audio.set_active(True)
        self.audio.transition(462625000, False)
        quiet = bytes(PCM_BYTES)
        voice = b'\x00\x08' * 320
        for i in range(5):
            self.audio.publish(quiet, False, now=100 + i * .02)
        self.assertEqual(self.audio.recordings(), [])
        for i in range(3):
            self.audio.publish(voice, True, now=100.1 + i * .02)
        for i in range(6):
            self.audio.publish(quiet, False, now=100.16 + i * .02)
        recordings = self.audio.recordings()
        self.assertEqual(len(recordings), 1)
        record = recordings[0]
        self.assertEqual(record['frequency_mhz'], 462.625)
        with wave.open(str(self.audio.recording_path(record['filename']))) as stream:
            self.assertEqual((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()), (1, 2, 16000))
            pcm = stream.readframes(stream.getnframes())
        self.assertTrue(pcm.startswith(quiet * 5))
        self.assertIn(voice * 3, pcm)
        self.assertFalse(self.audio.status()['recording_active'])
        self.audio.transition(467637500, False)
        self.audio.publish(voice, True, now=200)
        self.audio.finish_visit()
        self.assertEqual({r['frequency_mhz'] for r in self.audio.recordings()}, {462.625, 467.6375})

    def test_quota_stops_recording_without_stopping_stream(self):
        self.audio.configure(True, 100, .1)
        self.audio.set_active(True)
        listener = self.audio.subscribe()
        self.audio.publish(bytes(PCM_BYTES), True, now=100)
        self.assertEqual(len(listener.get_nowait()), FRAME_BYTES)
        self.assertTrue(self.audio.active)
        self.assertFalse(self.audio.enabled)
        self.assertIn('recording limit', self.audio.warning)
        self.assertEqual(self.audio.recordings(), [])

    def test_unsafe_recording_paths_are_rejected(self):
        for name in ('../hermes.bin', '/etc/passwd', '..%2ffile.wav', 'x.wav/../../x', 'x.wav.part'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.audio.recording_path(name)

    def test_recording_configuration_validation(self):
        for value in ({'record_audio': 'yes'}, {'recording_limit_mb': 0},
                      {'recording_limit_mb': float('inf')}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config(value)
