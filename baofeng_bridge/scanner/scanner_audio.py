"""Bounded live audio fanout and optional host-only speech recordings."""

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import re
import shutil
import struct
import threading
import time
import uuid
import wave

SAMPLE_RATE = 16000
PCM_BYTES = 640
HEADER = struct.Struct('<dIB3x')  # epoch seconds, frequency Hz, flags, padding
FRAME_BYTES = HEADER.size + PCM_BYTES
TUNING, VOICE, NO_DATA = 1, 2, 4


class ScannerAudio:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.listeners = set()
        self.active = False
        self.frequency = 0
        self.tuning = True
        self.enabled = False
        self.quota = 1024 * 1024 * 1024
        self.used = self._disk_usage()
        self.warning = ''
        self.pre_roll = deque(maxlen=25)
        self.writer = None
        self.record = None
        self.last_voice = None
        self.grace = 3

    def _disk_usage(self):
        return sum(path.stat().st_size for path in self.directory.iterdir()
                   if path.is_file() and (path.name.endswith('.wav') or path.name.endswith('.wav.part')))

    def configure(self, enabled, max_bytes, grace):
        with self.lock:
            self.finish_visit()
            self.enabled = enabled
            self.quota = max_bytes
            self.grace = grace
            self.used = self._disk_usage()
            self.warning = ''

    def set_active(self, value):
        with self.lock:
            self.active = value
            if value:
                self.frequency = 0
                self.tuning = True
            if not value:
                self.finish_visit()
                for listener in tuple(self.listeners):
                    self._offer(listener, None)

    @staticmethod
    def _offer(listener, value):
        try:
            listener.put_nowait(value)
        except queue.Full:
            try:
                listener.get_nowait()
            except queue.Empty:
                pass
            try:
                listener.put_nowait(value)
            except queue.Full:
                pass

    def subscribe(self):
        with self.lock:
            if not self.active:
                raise RuntimeError('Start the scanner to hear live audio')
            if len(self.listeners) >= 8:
                raise RuntimeError('Too many simultaneous listeners')
            listener = queue.Queue(maxsize=50)  # At most one second per browser.
            self.listeners.add(listener)
            return listener

    def unsubscribe(self, listener):
        with self.lock:
            self.listeners.discard(listener)

    def transition(self, frequency, tuning):
        with self.lock:
            self.finish_visit()
            self.frequency = frequency
            self.tuning = tuning

    def heartbeat(self):
        with self.lock:
            flags = NO_DATA | (TUNING if self.tuning else 0)
            return HEADER.pack(time.time(), self.frequency, flags) + bytes(PCM_BYTES)

    def publish(self, pcm, speech, now=None):
        if len(pcm) != PCM_BYTES:
            return
        now = time.time() if now is None else now
        with self.lock:
            if not self.active:
                return
            flags = VOICE if speech else 0
            packet = HEADER.pack(now, self.frequency, flags) + pcm
            for listener in tuple(self.listeners):
                self._offer(listener, packet)
            self.pre_roll.append((now, pcm))
            if not self.enabled:
                return
            try:
                if speech:
                    self.last_voice = now
                if self.writer is None and speech:
                    self._begin(now)
                    for _, audio in self.pre_roll:
                        self._write(audio)
                elif self.writer is not None:
                    self._write(pcm)
                if self.writer is not None and (
                        now - self.last_voice >= self.grace or self.record['frames'] >= SAMPLE_RATE * 60):
                    self._finish(now)
            except (OSError, wave.Error) as error:
                self.warning = 'Recording paused: {}'.format(error)
                self.enabled = False
                self._finish(now)

    def _begin(self, now):
        if shutil.disk_usage(self.directory).free < 32 * 1024 * 1024:
            raise OSError('low free disk space on the host')
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S')
        name = '{}-{}-{}.wav'.format(stamp, self.frequency, uuid.uuid4().hex[:8])
        self.record = {'filename': name, 'frequency_mhz': self.frequency / 1e6,
                       'started_at': self.pre_roll[0][0] if self.pre_roll else now, 'frames': 0}
        self.writer = wave.open(str(self.directory / (name + '.part')), 'wb')
        self.writer.setnchannels(1)
        self.writer.setsampwidth(2)
        self.writer.setframerate(SAMPLE_RATE)

    def _write(self, pcm):
        if self.used + 44 + self.record['frames'] * 2 + len(pcm) > self.quota:
            raise OSError('recording limit reached on the host')
        self.writer.writeframesraw(pcm)
        self.record['frames'] += len(pcm) // 2

    def _finish(self, now):
        if self.writer is None:
            return
        writer, record = self.writer, self.record
        self.writer = self.record = None
        try:
            writer.close()
            temporary = self.directory / (record['filename'] + '.part')
            if not record['frames']:
                temporary.unlink(missing_ok=True)
                return
            path = self.directory / record['filename']
            temporary.replace(path)
            record.update(ended_at=now, duration_seconds=round(record.pop('frames') / SAMPLE_RATE, 3),
                          bytes=path.stat().st_size)
            self.used += record['bytes']
            path.with_suffix('.json').write_text(json.dumps(record, ensure_ascii=False) + '\n')
        except (OSError, wave.Error) as error:
            self.enabled = False
            self.warning = 'Could not finish the recording on the host: {}'.format(error)

    def finish_visit(self):
        with self.lock:
            self._finish(time.time())
            self.pre_roll.clear()
            self.last_voice = None

    def status(self):
        with self.lock:
            current = self.record['frames'] * 2 + 44 if self.record else 0
            return {'live_available': self.active, 'listeners': len(self.listeners),
                    'recording_enabled': self.enabled, 'recording_active': self.writer is not None,
                    'used_mb': round((self.used + current) / 1024 / 1024, 2),
                    'limit_mb': round(self.quota / 1024 / 1024), 'warning': self.warning,
                    'storage': 'host', 'format': 'WAV PCM mono 16 kHz', 'frame_bytes': FRAME_BYTES}

    def recordings(self):
        result = []
        for path in sorted(self.directory.glob('*.json'), reverse=True)[:50]:
            try:
                record = json.loads(path.read_text())
                if self.recording_path(record['filename']).is_file():
                    result.append(record)
            except (OSError, ValueError, KeyError):
                continue
        return result

    def recording_path(self, name):
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.wav', name):
            raise ValueError('Invalid recording name')
        return self.directory / name
