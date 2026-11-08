#!/usr/bin/env python3
"""Host-side single-slot radio scanner and dashboard control API."""

import argparse
from collections import deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import math
import queue
from pathlib import Path
import signal
import socket
import struct
import threading
import time
from urllib.parse import unquote

from baofeng_bridge.scanner.radio_programming import C3Radio, CHANNEL_ONE, MEMORY_SIZE, RadioCommunicationError, scanner_channel
from baofeng_bridge.scanner.scanner_audio import ScannerAudio
from baofeng_bridge.paths import PROJECT_ROOT

FREQUENCIES = tuple(start + step * 12500
                    for start in (462562500, 467562500) for step in range(13))
DEFAULT_FREQUENCIES = [462625000] + [freq for freq in FREQUENCIES if freq != 462625000]
# BF-777S range from its manual. This is not a
# measurement of RF sensitivity at the limits of this particular unit.
RECEIVER_MIN_HZ = 400000000
RECEIVER_MAX_HZ = 470000000
# RX frequencies in channel order from the factory image of our BF-888-protocol unit.
# Yours may ship with different defaults: read yours with chirp_download.py and edit this list.
FACTORY_FREQUENCIES = [462.125, 462.225, 462.325, 462.425, 462.525, 462.625, 462.725, 462.825, 462.925, 463.025, 463.125, 463.225, 463.525, 450.225, 460.325, 469.95]
FREQUENCY_PRESETS = {
    'factory': {
        'label': 'Baofeng factory · all frequencies',
        'description': 'All frequencies from the factory backup; RX only.',
        'frequencies_mhz': FACTORY_FREQUENCIES,
    },
    'anatel_general': {
        'label': 'Brazil (Anatel) · all general-use channels',
        'description': '26 distinct frequencies (Brazilian general-use list); steps through automatically.',
        'frequencies_mhz': [freq / 1e6 for freq in FREQUENCIES],
    },
    'full_band': {
        'label': 'BF-777S nominal band · RX only',
        'description': '400–470 MHz; 12.5 or 25 kHz steps. Slow sweep by reprogramming.',
    },
}
STATE_DIR = PROJECT_ROOT / 'diagnostics/scanner'
MIN_WRITE_INTERVAL = 15.0
HEADER = struct.Struct('<IHH')
SAMPLES = struct.Struct('<320h')


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def validate_config(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid configuration')
    preset = value.get('frequency_preset')
    if preset == 'factory_anatel':  # Migrate the old filtered preset and cached browsers.
        preset = 'factory'
    if preset is not None and preset not in (*FREQUENCY_PRESETS, 'custom'):
        raise ValueError('Invalid frequency preset')
    sweep = {}
    if preset == 'full_band':
        bounds = []
        for key, default in (('sweep_start_mhz', 400), ('sweep_end_mhz', 470)):
            number = value.get(key, default)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise ValueError('Invalid sweep limit')
            hz = round(number * 1e6)
            if abs(number * 1e6 - hz) > .01 or not RECEIVER_MIN_HZ <= hz <= RECEIVER_MAX_HZ or hz % 12500:
                raise ValueError('Use limits between 400 and 470 MHz on a 12.5 kHz grid')
            bounds.append(hz)
            sweep[key] = hz / 1e6
        step = value.get('sweep_step_khz', 12.5)
        if isinstance(step, bool) or step not in (12.5, 25):
            raise ValueError('Passo permitido: 12,5 ou 25 kHz')
        step_hz = round(step * 1000)
        if bounds[1] < bounds[0] or (bounds[1] - bounds[0]) % step_hz:
            raise ValueError('Align the end of the band with the chosen step')
        sweep['sweep_step_khz'] = step
        incoming = [hz / 1e6 for hz in range(bounds[0], bounds[1] + 1, step_hz)]
    elif preset in FREQUENCY_PRESETS:
        incoming = FREQUENCY_PRESETS[preset]['frequencies_mhz']
    else:
        incoming = value.get('frequencies_mhz', FREQUENCY_PRESETS['factory']['frequencies_mhz'])
    maximum = 5601 if preset == 'full_band' else 256
    if not isinstance(incoming, list) or not 1 <= len(incoming) <= maximum:
        raise ValueError('Invalid number of frequencies')
    frequencies = []
    for item in incoming:
        if isinstance(item, bool):
            raise ValueError('Invalid frequency')
        try:
            number = float(item)
            frequency = round(number * 1e6)
        except (ValueError, TypeError, OverflowError):
            raise ValueError('Invalid frequency') from None
        if not math.isfinite(number) or abs(number * 1e6 - frequency) > .01 or not RECEIVER_MIN_HZ <= frequency <= RECEIVER_MAX_HZ or frequency % 12500:
            raise ValueError('Use RX frequencies between 400 and 470 MHz on a 12.5 kHz grid')
        if frequency not in frequencies:
            frequencies.append(frequency)
    if preset is None:
        if frequencies == [round(f * 1e6) for f in FACTORY_FREQUENCIES]:
            preset = 'factory'
        elif frequencies in (DEFAULT_FREQUENCIES, list(FREQUENCIES)):
            preset = 'anatel_general'
        else:
            preset = 'custom'
    resolved = (list(FREQUENCY_PRESETS[preset]['frequencies_mhz'])
                if preset in ('factory', 'anatel_general') else [f / 1e6 for f in frequencies])
    config = {'frequency_preset': preset, 'frequencies_mhz': resolved, **sweep}
    for name, default, low, high in (('listen_seconds', 5, 2, 60),
                                    ('grace_seconds', 3, 1, 60),
                                    ('max_hold_seconds', 120, 15, 900),
                                    ('rms_floor', 250, 0, 10000)):
        number = value.get(name, default)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError('{} outside the range {}–{}'.format(name, low, high))
        config[name] = number
    if config['max_hold_seconds'] < max(config['listen_seconds'], config['grace_seconds']):
        raise ValueError('The per-frequency limit must exceed the listen and grace times')
    repeat = value.get('repeat', False)
    if not isinstance(repeat, bool):
        raise ValueError('Invalid repeat option')
    config['repeat'] = repeat
    record_audio = value.get('record_audio', False)
    if not isinstance(record_audio, bool):
        raise ValueError('Invalid recording option')
    limit = value.get('recording_limit_mb', 1024)
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or not 16 <= limit <= 102400:
        raise ValueError('Recording limit must be between 16 and 102400 MB')
    config.update(record_audio=record_audio, recording_limit_mb=limit)
    return config


class Dwell:
    """Advance only after valid audio, minimum dwell, and trailing quiet."""
    def __init__(self, config, now):
        self.config = config
        self.started = now
        self.last_voice = None
        self.last_packet = None
        self.voice_frames = 0
        self.frames = 0
        self.max_rms = 0
        self.votes = deque(maxlen=5)
        self.voiced = False
        self.hits = 0

    def feed(self, now, speech, rms):
        self.last_packet = now
        self.frames += 1
        self.max_rms = max(self.max_rms, rms)
        self.votes.append(bool(speech and rms >= self.config['rms_floor']))
        if len(self.votes) == 5 and self.votes[-1] and sum(self.votes) >= 3:
            if not self.voiced:
                self.hits += 1
            self.voiced = True
            self.last_voice = now
            self.voice_frames += 1
        elif self.voiced and self.last_voice is not None and now - self.last_voice > .3:
            self.voiced = False

    def reason(self, now):
        if self.last_packet is None or now - self.last_packet > 2:
            if now - self.started > 5:
                raise RuntimeError('No RX audio; scan stopped without marking the channel as clear')
            return None
        # Configuration EEPROM is rewritten only after this lower bound.
        if now - self.started < MIN_WRITE_INTERVAL:
            return None
        if now - self.started >= self.config['max_hold_seconds']:
            return 'hold limit'
        if now - self.started < self.config['listen_seconds']:
            return None
        if now - (self.last_voice if self.last_voice is not None else self.started) >= self.config['grace_seconds']:
            return 'silence after voice' if self.last_voice is not None else 'no voice detected'
        return None


class Scanner:
    def __init__(self, state_dir, port, hermes_profile=None, radio_factory=C3Radio, vad_factory=None):
        self.directory = Path(state_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'state.json'
        self.profile_path = self.directory / 'hermes.bin'
        if hermes_profile is not None:
            image = Path(hermes_profile).read_bytes()
            if len(image) != MEMORY_SIZE:
                raise ValueError('The Hermes profile must be a 992-byte binary dump')
            if self.profile_path.exists() and self.profile_path.read_bytes() != image:
                raise ValueError('A different Hermes profile is already saved; it was not overwritten')
            if not self.profile_path.exists():
                self.profile_path.write_bytes(image)
        self.hermes = self.profile_path.read_bytes() if self.profile_path.exists() else None
        self.port = port
        self.radio_factory = radio_factory
        self.vad_factory = vad_factory
        if self.vad_factory is None:
            try:
                import webrtcvad
                self.vad_factory = lambda: webrtcvad.Vad(2)
            except ImportError:
                pass
        self.vad = None
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.dwell = None
        self.settle_until = 0
        self.sequence = None
        self.last_persist = 0
        self.audio = ScannerAudio(self.directory / 'recordings')
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        profile = saved.get('profile', 'hermes')
        self.state = {
            'profile': profile, 'phase': 'idle' if profile == 'hermes' else 'recovery_required',
            'running': False, 'frequency_mhz': None, 'error': '',
            'frequency_index': None,
            'config': validate_config(saved.get('config', {})),
            'results': saved.get('results', []), 'history': saved.get('history', [])[-100:],
            'hermes_sha256': hashlib.sha256(self.hermes).hexdigest() if self.hermes else None,
            'minimum_retune_seconds': MIN_WRITE_INTERVAL,
            'detector': 'WebRTC VAD (local, no transcription)',
        }
        self._persist()

    def _persist(self):
        atomic_json(self.path, self.state)
        self.last_persist = time.monotonic()

    def update(self, **changes):
        with self.lock:
            self.state.update(changes)
            self._persist()

    def event(self, message):
        with self.lock:
            self.state['history'].append({'ts': time.time(), 'message': message})
            self.state['history'] = self.state['history'][-100:]
            self._persist()

    def status(self):
        with self.lock:
            value = json.loads(json.dumps(self.state))
            value['results'] = value['results'][-200:]
            value['available'] = self.hermes is not None and self.vad_factory is not None
            value['profile_saved'] = self.hermes is not None
            value['vad_available'] = self.vad_factory is not None
            value['supported_frequencies_mhz'] = [f / 1e6 for f in DEFAULT_FREQUENCIES]
            value['frequency_presets'] = [dict(id=key, **preset)
                                          for key, preset in FREQUENCY_PRESETS.items()]
            value['receiver_band'] = {'start_mhz': 400, 'end_mhz': 470,
                                      'steps_khz': [12.5, 25], 'rf_limits_verified': False}
            count = len(value['config']['frequencies_mhz'])
            value['scan_plan'] = {'frequency_count': count,
                                  'current_index': value.get('frequency_index'),
                                  'minimum_listening_seconds': count * MIN_WRITE_INTERVAL,
                                  'profile_uploads_including_restore': count + 1}
            value['audio'] = self.audio.status()
            value['recordings'] = self.audio.recordings()
            if self.dwell is not None:
                value['current'] = {
                    'elapsed_seconds': round(time.monotonic() - self.dwell.started, 1),
                    'speech_detected': self.dwell.last_voice is not None,
                    'speech_active': self.dwell.voiced,
                    'speech_seconds': round(self.dwell.voice_frames * .02, 2),
                    'rms': self.dwell.max_rms,
                    'audio_age_seconds': (round(time.monotonic() - self.dwell.last_packet, 1)
                                          if self.dwell.last_packet is not None else None),
                }
            return value

    def start(self, config):
        config = validate_config(config)
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError('A radio operation is already in progress')
            if self.state['profile'] != 'hermes':
                raise RuntimeError('Restore the Hermes profile before starting another scan')
            if self.hermes is None or self.vad_factory is None:
                raise RuntimeError('Hermes profile or voice detector unavailable')
            self.stop_event.clear()
            self.audio.configure(config['record_audio'], round(config['recording_limit_mb'] * 1024 * 1024),
                                 config['grace_seconds'])
            self.audio.set_active(True)
            self.state.update(profile='scanner', phase='preparing', running=True,
                              config=config, results=[], error='', frequency_mhz=None, frequency_index=None)
            self._persist()  # Relay stops forwarding to Hermes before any retune.
            self.worker = threading.Thread(target=self._run, name='radio-scanner', daemon=True)
            self.worker.start()

    def stop(self):
        with self.lock:
            self.stop_event.set()
            if self.worker is not None and self.worker.is_alive():
                self.update(phase='stopping')
                return
            if self.state['profile'] == 'hermes':
                return
            self.update(phase='restoring', running=True)
            self.worker = threading.Thread(target=self._recover, name='radio-restore', daemon=True)
            self.worker.start()

    def set_recording(self, enabled, limit_mb):
        with self.lock:
            config = validate_config(dict(self.state['config'], record_audio=enabled,
                                          recording_limit_mb=limit_mb))
            self.audio.configure(enabled, round(config['recording_limit_mb'] * 1024 * 1024),
                                 config['grace_seconds'])
            self.update(config=config)

    def feed(self, packet, now=None):
        if len(packet) != HEADER.size + 640:
            return
        magic, sequence, count = HEADER.unpack_from(packet)
        if magic != 0x37374642 or count != 320:
            return
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.state['phase'] != 'listening' or self.dwell is None or now < self.settle_until:
                return
            if self.sequence is not None and not 0 < ((sequence - self.sequence) & 0xffff) < 0x8000:
                return
            self.sequence = sequence
            pcm = packet[HEADER.size:]
            samples = SAMPLES.unpack(pcm)
            rms = math.isqrt(sum(sample * sample for sample in samples) // 320)
            self.dwell.feed(now, self.vad.is_speech(pcm, 16000), rms)
            self.audio.publish(pcm, self.dwell.voiced)

    def _verify_other_channels(self, image):
        if image[:0x10] != self.hermes[:0x10] or image[0x20:] != self.hermes[0x20:]:
            raise RuntimeError('Memories outside channel 1 changed; automatic restore blocked')

    def _retry_communication(self, operation, label):
        for attempt in range(1, 4):
            try:
                return operation()
            except RadioCommunicationError as error:
                if attempt == 3:
                    raise RadioCommunicationError('{}: 3 attempts failed: {}'.format(label, error)) from error
                self.event('{}: communication failure ({}); retry {}/3.'.format(
                    label, error, attempt + 1))
                time.sleep(.5 * attempt)

    def _read_profile(self, radio, label):
        def read():
            with radio.programming():
                return radio.read_image()
        return self._retry_communication(read, label)

    def _write_verified(self, radio, target, label):
        def upload():
            # Retry the complete upload in a fresh session, never a block in
            # a session whose protocol state is now unknown.
            with radio.programming():
                radio.write_image(target)
            with radio.programming():
                actual = radio.read_image()
            if actual != target:
                raise RuntimeError('Readback after upload does not match the profile: ' + label)
            return actual
        return self._retry_communication(upload, label)

    def _restore(self, radio):
        self.audio.set_active(False)
        self.update(phase='restoring', frequency_mhz=None, frequency_index=None)
        with self.lock:
            self.dwell = None
        radio.receive_only(True)
        image = self._read_profile(radio, 'Read before restoring Hermes')
        self._verify_other_channels(image)
        if image != self.hermes:
            restored = self._write_verified(radio, self.hermes, 'Restore Hermes')
        else:
            restored = self._read_profile(radio, 'Verify Hermes')
        if restored != self.hermes:
            raise RuntimeError('Hermes profile restore did not verify byte for byte')
        # Unmute only after the complete 992-byte comparison and bridge exit.
        radio.receive_only(False)
        self.update(profile='hermes', phase='idle', running=False, frequency_mhz=None)
        self.event('Hermes restored: 992 bytes verified; PTT released for normal use.')

    def _recover(self):
        radio = None
        try:
            radio = self.radio_factory(self.port).open()
            self._restore(radio)
        except Exception as error:
            logging.exception('Scanner recovery failed')
            self.update(profile='scanner', phase='recovery_required', running=False, error=str(error))
        finally:
            if radio is not None:
                radio.close()

    def _run(self):
        radio = None
        verified = False
        try:
            radio = self.radio_factory(self.port).open()
            radio.receive_only(True)
            image = self._read_profile(radio, 'Read initial profile')
            if image != self.hermes:
                # Do not overwrite an externally changed configuration.
                raise RuntimeError('Radio differs from the saved Hermes profile; check the backup before using it')
            verified = True
            (self.directory / ('before-scan-{}.bin'.format(time.time_ns()))).write_bytes(image)
            config = self.state['config']
            pass_number = 0
            while not self.stop_event.is_set():
                pass_number += 1
                for frequency_index, frequency_mhz in enumerate(config['frequencies_mhz'], start=1):
                    if self.stop_event.is_set():
                        break
                    frequency = round(frequency_mhz * 1e6)
                    self.audio.transition(frequency, tuning=True)
                    self.update(phase='tuning', frequency_mhz=frequency_mhz,
                                frequency_index=frequency_index, pass_number=pass_number)
                    target = bytearray(self.hermes)
                    target[CHANNEL_ONE] = scanner_channel(self.hermes, frequency)
                    self._write_verified(radio, bytes(target), 'Tune {:.4f} MHz'.format(frequency_mhz))
                    if self.stop_event.is_set():
                        break
                    with self.lock:
                        self.vad = self.vad_factory()
                        self.dwell = Dwell(config, time.monotonic())
                        self.settle_until = self.dwell.started + .6
                        self.sequence = None
                        self.audio.transition(frequency, tuning=False)
                        self.update(phase='listening')
                    self.event('Channel 1 at {:.4f} MHz; waiting for voice.'.format(frequency_mhz))
                    reason = None
                    while not self.stop_event.wait(.05):
                        with self.lock:
                            reason = self.dwell.reason(time.monotonic())
                        if reason:
                            break
                    with self.lock:
                        dwell = self.dwell
                        result = {'frequency_mhz': frequency_mhz, 'ts': time.time(),
                                  'pass_number': pass_number, 'speech_detected': dwell.last_voice is not None,
                                  'speech_seconds': round(dwell.voice_frames * .02, 2),
                                  'speech_hits': dwell.hits, 'max_rms': dwell.max_rms,
                                  'listened_seconds': round(time.monotonic() - dwell.started, 2),
                                  'reason': reason or 'interrompido'}
                        self.state['results'].append(result)
                        self.state['results'] = self.state['results'][-1000:]
                        self.dwell = None
                        self.audio.finish_visit()
                        self.update(phase='tuning')
                    self.event('{:.4f} MHz: {}.'.format(frequency_mhz, result['reason']))
                if not config['repeat']:
                    break
        except Exception as error:
            logging.exception('Scanner failed')
            self.update(error=str(error))
            self.event('Scan stopped: {}'.format(error))
        finally:
            self.audio.set_active(False)
            if radio is not None:
                if not verified:
                    # No channel was changed by this run. Do not silently
                    # overwrite a pre-existing channel-1 edit during cleanup.
                    self.update(profile='scanner', phase='recovery_required', running=False)
                    radio.close()
                    return
                try:
                    self._restore(radio)
                except Exception as error:
                    logging.exception('Hermes restore failed')
                    self.update(profile='scanner', phase='recovery_required', running=False, error=str(error))
                    self.event('Restore pending; TX stays inhibited.')
                finally:
                    radio.close()
            else:
                self.update(profile='scanner', phase='recovery_required', running=False)


def handler_for(scanner, allowed_clients):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            if ipaddress.ip_address(self.client_address[0]) not in allowed_clients:
                self.reply(403, {'error': 'Client not authorized'})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            if self.path == '/api/audio':
                try:
                    listener = scanner.audio.subscribe()
                except RuntimeError as error:
                    self.reply(409, {'error': str(error)})
                    return
                try:
                    self.connection.settimeout(5)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.send_header('X-Audio-Format', 'pcm_s16le_16000_mono_with_metadata')
                    self.end_headers()
                    while scanner.audio.active:
                        try:
                            packet = listener.get(timeout=.1)
                        except queue.Empty:
                            packet = scanner.audio.heartbeat()
                        if packet is None:
                            break
                        self.wfile.write(packet)
                        self.wfile.flush()
                except (OSError, ConnectionError):
                    pass
                finally:
                    scanner.audio.unsubscribe(listener)
                return
            if self.path.startswith('/api/recordings/'):
                try:
                    path = scanner.audio.recording_path(unquote(self.path[len('/api/recordings/'):]))
                    source = path.open('rb')
                except (OSError, ValueError):
                    self.reply(404, {'error': 'Recording not found'})
                    return
                with source:
                    self.send_response(200)
                    self.send_header('Content-Type', 'audio/wav')
                    self.send_header('Content-Length', str(path.stat().st_size))
                    self.send_header('Content-Disposition', 'attachment; filename="{}"'.format(path.name))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    try:
                        while chunk := source.read(65536):
                            self.wfile.write(chunk)
                    except OSError:
                        pass
                return
            if self.path != '/api/status':
                self.reply(404, {'error': 'Not found'})
                return
            self.reply(200, scanner.status())

        def do_POST(self):
            if not self.allowed():
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 8192:
                    raise ValueError('Request too large')
                data = json.loads(self.rfile.read(size) or b'{}')
                if self.path == '/api/start':
                    scanner.start(data)
                elif self.path == '/api/recording':
                    scanner.set_recording(data.get('enabled'), data.get('limit_mb', 1024))
                elif self.path in ('/api/stop', '/api/hermes'):
                    scanner.stop()
                else:
                    self.reply(404, {'error': 'Not found'})
                    return
                self.reply(202, scanner.status())
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self.reply(400, {'error': str(error)})
            except RuntimeError as error:
                self.reply(409, {'error': str(error)})

        def log_message(self, *_args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyUSB0')
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--http-port', type=int, default=8788)
    parser.add_argument('--audio-port', type=int, default=7781)
    parser.add_argument('--allow-client', action='append', default=['127.0.0.1'])
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
    parser.add_argument('--hermes-profile', type=Path, help='initial raw 992-byte Hermes backup')
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.state_dir / 'scanner.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    scanner = Scanner(args.state_dir, args.port, args.hermes_profile)
    audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    audio.bind(('127.0.0.1', args.audio_port))
    audio.settimeout(.5)
    shutdown = threading.Event()

    def receive():
        while not shutdown.is_set():
            try:
                packet, _ = audio.recvfrom(2048)
            except socket.timeout:
                continue
            try:
                scanner.feed(packet)
            except Exception:
                logging.exception('Scanner audio detection failed')
                scanner.stop()

    threading.Thread(target=receive, name='scanner-audio', daemon=True).start()
    allowed = {ipaddress.ip_address(value) for value in args.allow_client}
    server = ThreadingHTTPServer((args.bind, args.http_port), handler_for(scanner, allowed))

    def stop_server(*_args):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print('Scanner API: http://{}:{}'.format(args.bind, args.http_port), flush=True)
    try:
        server.serve_forever()
    finally:
        scanner.stop()
        if scanner.worker is not None:
            scanner.worker.join(timeout=45)
        shutdown.set()
        audio.close()
        server.server_close()


if __name__ == '__main__':
    main()
