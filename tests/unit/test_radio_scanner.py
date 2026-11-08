"""Scanner timing, memory integrity, profile recovery and routing unit tests."""

from contextlib import contextmanager
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch

from baofeng_bridge.scanner import radio_scanner
from baofeng_bridge.scanner.radio_programming import C3Radio, UPLOAD_RANGES, RadioCommunicationError, scanner_channel
from baofeng_bridge.scanner.radio_scanner import Dwell, Scanner, validate_config
from baofeng_bridge.audio.udp_relay import ScannerRoute


class FakeRadio:
    def __init__(self, original):
        self.original = original
        self.image = bytearray(original)
        self.muted = False
        self.closed = False
        self.writes = []
        self.fail_tune_once = False
        self.fail_restore = False
        self.sessions = []
        self.session_ops = None

    def open(self):
        return self

    def close(self):
        self.closed = True

    def receive_only(self, enabled):
        self.muted = enabled

    @contextmanager
    def programming(self):
        assert self.muted
        operations = []
        self.session_ops = operations
        try:
            yield self
        finally:
            self.sessions.append(operations)
            self.session_ops = None

    def read_image(self):
        if self.session_ops is not None:
            self.session_ops.append('read')
        return bytes(self.image)

    def write_image(self, value):
        assert self.muted
        if self.session_ops is not None:
            self.session_ops.append('write')
        restoring = value == self.original
        self.writes.append((value, restoring))
        if restoring and self.fail_restore:
            raise RuntimeError('USB lost during restore')
        if self.fail_tune_once and not restoring:
            self.image[0x10:0x18] = value[0x10:0x18]
            self.fail_tune_once = False
            raise RuntimeError('second block failed')
        self.image[:] = value


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.image = bytes((i * 13) % 256 for i in range(992))
        self.profile = self.root / 'source.bin'
        self.profile.write_bytes(self.image)
        self.radio = FakeRadio(self.image)
        self.scanner = Scanner(self.root / 'state', 'fake', self.profile,
                               radio_factory=lambda _: self.radio,
                               vad_factory=lambda: None)

    def tearDown(self):
        self.temporary.cleanup()

    def config(self, **changes):
        config = {'frequencies_mhz': [462.625], 'repeat': False}
        config.update(changes)
        return validate_config(config)

    def run_scan(self):
        self.scanner.state.update(profile='scanner', config=self.config())
        with patch.object(Dwell, 'reason', return_value='no voice detected'):
            self.scanner._run()

    def test_only_channel_one_changes_and_complete_profile_is_restored(self):
        self.run_scan()
        self.assertEqual(self.radio.read_image(), self.image)
        self.assertEqual(len(self.radio.writes), 2)
        scan, restore = self.radio.writes
        self.assertFalse(scan[1])
        self.assertEqual(scan[0][0x14:0x1c], b'\xff' * 8)
        self.assertEqual(scan[0][:0x10], self.image[:0x10])
        self.assertEqual(scan[0][0x20:], self.image[0x20:])
        self.assertTrue(restore[1])
        self.assertEqual(restore[0], self.image)
        self.assertFalse(self.radio.muted)
        self.assertTrue(self.radio.closed)
        self.assertEqual(self.scanner.status()['profile'], 'hermes')
        self.assertEqual(self.profile.read_bytes(), self.image)
        for operations in self.radio.sessions:
            self.assertFalse('read' in operations and 'write' in operations)

    def test_transient_partial_upload_restarts_session_then_scans(self):
        write = self.radio.write_image
        failures = [True]
        def flaky(value):
            if failures:
                failures.pop()
                self.radio.image[0x10:0x18] = value[0x10:0x18]
                raise RadioCommunicationError('missing ACK')
            return write(value)
        with patch.object(self.radio, 'write_image', side_effect=flaky), patch('baofeng_bridge.scanner.radio_scanner.time.sleep'):
            self.run_scan()
        status = self.scanner.status()
        self.assertEqual(status['error'], '')
        self.assertEqual(len(status['results']), 1)
        self.assertTrue(any('retry 2/3' in e['message'] for e in status['history']))
        self.assertEqual(self.radio.read_image(), self.image)
        self.assertFalse(self.radio.muted)
        self.assertEqual(self.radio.sessions[1], [])  # failed session exited
        self.assertTrue(all(not ('read' in ops and 'write' in ops) for ops in self.radio.sessions))

    def test_transient_read_retries_without_any_write(self):
        self.radio.receive_only(True)
        with patch.object(self.radio, 'read_image', side_effect=[RadioCommunicationError('short reply'), self.image]) as read, patch('baofeng_bridge.scanner.radio_scanner.time.sleep'):
            self.assertEqual(self.scanner._read_profile(self.radio, 'Teste'), self.image)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(self.radio.writes, [])
        self.assertEqual(len(self.radio.sessions), 2)

    def test_persistent_ack_failure_stops_after_three_attempts_and_restores(self):
        write = self.radio.write_image
        attempts = []
        def broken(value):
            if value != self.image:
                attempts.append(value)
                self.radio.image[0x10:0x18] = value[0x10:0x18]
                raise RadioCommunicationError('missing ACK')
            return write(value)
        with patch.object(self.radio, 'write_image', side_effect=broken), patch('baofeng_bridge.scanner.radio_scanner.time.sleep'):
            self.run_scan()
        self.assertEqual(len(attempts), 3)
        self.assertIn('3 attempts failed', self.scanner.status()['error'])
        self.assertEqual(self.scanner.status()['profile'], 'hermes')
        self.assertEqual(self.radio.read_image(), self.image)
        self.assertFalse(self.radio.muted)

    def test_readback_mismatch_is_not_retried(self):
        self.radio.receive_only(True)
        with patch.object(self.radio, 'read_image', return_value=b'\x00' * 992), patch.object(self.radio, 'write_image') as write:
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                self.scanner._write_verified(self.radio, self.image, 'Teste')
        self.assertEqual(write.call_count, 1)
        self.assertTrue(self.radio.muted)

    def test_unconfirmed_session_exit_is_not_retried(self):
        self.radio.receive_only(True)
        @contextmanager
        def failed_exit():
            try:
                yield self.radio
            finally:
                raise RuntimeError('C3 programming exit not confirmed')
        with patch.object(self.radio, 'programming', side_effect=failed_exit) as session:
            with self.assertRaisesRegex(RuntimeError, 'exit not confirmed'):
                self.scanner._read_profile(self.radio, 'Teste')
        self.assertEqual(session.call_count, 1)
        self.assertTrue(self.radio.muted)

    def test_partial_retune_failure_restores_original(self):
        self.radio.fail_tune_once = True
        self.run_scan()
        self.assertEqual(self.radio.read_image(), self.image)
        self.assertEqual(self.scanner.status()['profile'], 'hermes')
        self.assertIn('second block', self.scanner.status()['error'])

    def test_failed_restore_keeps_rx_only_and_relay_suppressed(self):
        self.radio.fail_restore = True
        self.run_scan()
        self.assertTrue(self.radio.muted)
        self.assertEqual(self.scanner.status()['phase'], 'recovery_required')
        self.assertTrue(ScannerRoute(self.scanner.path).active())

    def test_preexisting_edit_is_never_overwritten_on_start_failure(self):
        self.radio.image[0x10] ^= 1
        edited = self.radio.read_image()
        self.run_scan()
        self.assertEqual(self.radio.read_image(), edited)
        self.assertEqual(self.radio.writes, [])
        self.assertTrue(self.radio.muted)

    def test_restore_refuses_to_overwrite_other_channel_edits(self):
        self.radio.image[0x20] ^= 1
        with self.assertRaisesRegex(RuntimeError, 'outside channel 1'):
            self.scanner._restore(self.radio)
        self.assertEqual(self.radio.writes, [])
        self.assertTrue(self.radio.muted)

    def test_restarted_controller_keeps_scanner_recovery_state(self):
        self.scanner.update(profile='scanner', phase='listening', running=True)
        restarted = Scanner(self.scanner.directory, 'fake', vad_factory=lambda: None)
        self.assertEqual(restarted.status()['phase'], 'recovery_required')
        self.assertFalse(restarted.status()['running'])
        self.assertTrue(ScannerRoute(restarted.path).active())

    def test_profile_cannot_be_replaced_implicitly(self):
        other = self.root / 'other.bin'
        other.write_bytes(b'\x00' * 992)
        with self.assertRaisesRegex(ValueError, 'was not overwritten'):
            Scanner(self.scanner.directory, 'fake', other)
        self.assertEqual(self.scanner.profile_path.read_bytes(), self.image)

    def test_config_rejects_out_of_scope_and_nonfinite_values(self):
        for config in ({'frequencies_mhz': [399.9875]}, {'frequencies_mhz': [True]},
                       {'frequencies_mhz': [float('nan')]}, {'grace_seconds': float('inf')},
                       {'repeat': 'yes'}, {'listen_seconds': 60, 'max_hold_seconds': 15}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)

    def test_channel_encoding_is_rx_only_nfm_without_tones(self):
        data = scanner_channel(self.image, 462625000)
        self.assertEqual(data[:4], bytes.fromhex('00252646'))
        self.assertEqual(data[4:12], b'\xff' * 8)
        self.assertEqual(data[12] & 0x1e, 0x06)
        self.assertEqual(data[13:], self.image[0x1d:0x20])

    def test_presets_resolve_on_server_without_manual_frequencies(self):
        factory = validate_config({'frequency_preset': 'factory'})
        self.assertEqual(factory['frequencies_mhz'], [462.125, 462.225, 462.325, 462.425, 462.525, 462.625, 462.725, 462.825, 462.925, 463.025, 463.125, 463.225, 463.525, 450.225, 460.325, 469.95])
        self.assertEqual(validate_config({'frequency_preset': 'factory_anatel'})['frequencies_mhz'], factory['frequencies_mhz'])
        general = validate_config({'frequency_preset': 'anatel_general'})
        self.assertEqual(len(general['frequencies_mhz']), 26)
        self.assertEqual(len(set(general['frequencies_mhz'])), 26)
        self.assertEqual(general['frequencies_mhz'][0], 462.5625)
        self.assertEqual(general['frequencies_mhz'][-1], 467.7125)
        self.assertEqual(validate_config({})['frequency_preset'], 'factory')

    def test_custom_list_is_preserved_and_invalid_preset_rejected(self):
        value = {'frequency_preset': 'custom', 'frequencies_mhz': [467.6, 462.5625]}
        self.assertEqual(validate_config(value)['frequencies_mhz'], value['frequencies_mhz'])
        with self.assertRaisesRegex(ValueError, 'preset'):
            validate_config({'frequency_preset': 'unknown'})
        # A named preset resolves from the server list, never from injected values.
        self.assertEqual(validate_config({'frequency_preset': 'anatel_general',
                         'frequencies_mhz': [462.125]})['frequencies_mhz'],
                         [freq / 1e6 for freq in radio_scanner.FREQUENCIES])

    def test_existing_saved_lists_are_migrated_without_losing_custom_order(self):
        value = validate_config({'frequencies_mhz': [f / 1e6 for f in radio_scanner.DEFAULT_FREQUENCIES]})
        self.assertEqual(value['frequency_preset'], 'anatel_general')
        custom = validate_config({'frequencies_mhz': [467.6, 462.625]})
        self.assertEqual(custom['frequency_preset'], 'custom')
        self.assertEqual(custom['frequencies_mhz'], [467.6, 462.625])

    def test_full_band_is_generated_server_side_with_inclusive_limits(self):
        full = validate_config({'frequency_preset': 'full_band'})
        self.assertEqual(len(full['frequencies_mhz']), 5601)
        self.assertEqual(full['frequencies_mhz'][0], 400)
        self.assertEqual(full['frequencies_mhz'][-1], 470)
        coarse = validate_config({'frequency_preset': 'full_band', 'sweep_step_khz': 25})
        self.assertEqual(len(coarse['frequencies_mhz']), 2801)
        partial = validate_config({'frequency_preset': 'full_band',
                                   'sweep_start_mhz': 450, 'sweep_end_mhz': 450.025})
        self.assertEqual(partial['frequencies_mhz'], [450, 450.0125, 450.025])

    def test_sweep_rejects_invalid_or_out_of_band_limits(self):
        for fields in ({'sweep_start_mhz': 399}, {'sweep_end_mhz': 480},
                       {'sweep_start_mhz': 460, 'sweep_end_mhz': 450},
                       {'sweep_step_khz': 6.25}, {'sweep_step_khz': True},
                       {'sweep_end_mhz': float('nan')}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_config({'frequency_preset': 'full_band', **fields})

    def test_custom_receive_list_can_include_other_nominal_uhf_channels(self):
        self.assertEqual(validate_config({'frequency_preset': 'custom',
                         'frequencies_mhz': [450.225, 469.95]})['frequencies_mhz'],
                         [450.225, 469.95])


class UploadProtocolTests(unittest.TestCase):
    def test_full_upload_matches_chirp_order_without_reads(self):
        image = bytes(index % 256 for index in range(992))
        writes = []

        class Port:
            def write(self, data):
                writes.append(data)

            def read(self, count):
                self.assert_size = count
                if count != 1:
                    raise AssertionError('Upload interleaved a memory read')
                return b'\x06'

        radio = C3Radio('fake')
        radio.port = Port()
        radio.write_image(image)
        addresses = [address for start, end in UPLOAD_RANGES for address in range(start, end, 8)]
        self.assertEqual(len(writes), 48)
        self.assertEqual(addresses[:3], [0, 8, 0x10])
        self.assertEqual(addresses[34], 0x380)
        self.assertEqual(addresses[-2:], [0x2b0, 0x2b8])
        for command, address in zip(writes, addresses):
            self.assertEqual(command, struct.pack('>cHB', b'W', address, 8) + image[address:address + 8])

    def test_bad_ack_identifies_the_failed_block_and_reply(self):
        class Port:
            def __init__(self):
                self.address = None

            def write(self, data):
                self.address = int.from_bytes(data[1:3], 'big')

            def read(self, count):
                return b'\x15' if self.address == 0x380 else b'\x06'

        radio = C3Radio('fake')
        radio.port = Port()
        with self.assertRaisesRegex(RuntimeError, '0380.*received 15'):
            radio.write_image(bytes(992))


class DwellTests(unittest.TestCase):
    def setUp(self):
        self.dwell = Dwell(validate_config({'frequencies_mhz': [462.625]}), 100)

    def test_no_stream_is_an_error_not_a_free_channel(self):
        with self.assertRaisesRegex(RuntimeError, 'No RX audio'):
            self.dwell.reason(106)

    def test_quiet_channels_respect_write_pacing(self):
        self.dwell.feed(104, False, 50)
        self.assertIsNone(self.dwell.reason(104))
        self.dwell.feed(115, False, 50)
        self.assertEqual(self.dwell.reason(115), 'no voice detected')

    def test_speech_extends_hold_and_trailing_grace(self):
        for now in (114.90, 114.92, 114.94, 114.96, 114.98):
            self.dwell.feed(now, True, 1000)
        self.assertIsNone(self.dwell.reason(115))
        self.dwell.feed(117, False, 20)
        self.assertIsNone(self.dwell.reason(117))
        self.dwell.feed(118, False, 20)
        self.assertEqual(self.dwell.reason(118), 'silence after voice')

    def test_brief_squelch_click_does_not_count_as_speech(self):
        for index in range(5):
            self.dwell.feed(101 + index * .02, index == 0, 2000 if index == 0 else 20)
        self.assertIsNone(self.dwell.last_voice)


class RelayTests(unittest.TestCase):

    def test_profile_routing_and_fail_closed_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            clock = [0]
            route = ScannerRoute(path, clock=lambda: clock[0])
            self.assertFalse(route.active())
            path.write_text('{"profile":"scanner"}')
            clock[0] += 1
            self.assertTrue(route.active())
            path.unlink()
            clock[0] += 1
            self.assertTrue(route.active())
            path.write_text('broken json')
            clock[0] += 1
            self.assertTrue(route.active())
            path.write_text('{"profile":"hermes"}')
            clock[0] += 1
            self.assertFalse(route.active())
