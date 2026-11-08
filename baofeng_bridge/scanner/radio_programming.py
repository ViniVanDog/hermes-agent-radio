"""BF-888 clone protocol over the C3 scanner bridge; no CHIRP dependency."""

from contextlib import contextmanager
import logging
import struct
import time

import serial

MEMORY_SIZE = 0x3E0
CHANNEL_ONE = slice(0x10, 0x20)
# Exact order used by CHIRP H777Radio._ranges. This includes every channel
# and the known settings regions, not unmapped/calibration bytes in the dump.
UPLOAD_RANGES = ((0x0000, 0x0110), (0x0380, 0x03E0), (0x02B0, 0x02C0))


class RadioCommunicationError(RuntimeError):
    """A transient protocol failure after which a fresh session may be tried."""


class C3Radio:
    def __init__(self, port):
        self.port_name = port
        self.port = None

    def open(self):
        self.port = serial.Serial(baudrate=115200, timeout=.2, exclusive=True)
        self.port.dtr = False
        self.port.rts = False
        self.port.port = self.port_name
        self.port.open()
        deadline = time.monotonic() + 25
        fast_bridge = False
        queried = False
        while time.monotonic() < deadline:
            line = self.port.readline()
            logging.debug('C3 boot %r', line)
            if b'q - programming bridge' in line:
                fast_bridge = True
            if fast_bridge and b'h - show this help' in line:
                return self
            # Ask for capabilities only once normal audio mode is observed.
            # Sending periodic commands into an old bridge would keep it open.
            if not queried and line.startswith(b'RX window_ms='):
                self.port.write(b'h')
                queried = True
        self.close()
        raise RuntimeError('C3 scanner firmware is not ready (q/r/a commands required)')

    def close(self):
        if self.port is not None:
            self.port.close()
            self.port = None

    def console(self, command, expected):
        self.port.reset_input_buffer()
        self.port.write(command)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            line = self.port.readline()
            logging.debug('C3 console %r', line)
            if expected in line:
                return
        raise RuntimeError('C3 did not confirm {}'.format(expected.decode()))

    def receive_only(self, enabled):
        if enabled:
            self.console(b'r', b'PROFILE SCANNER RX_ONLY=1')
        else:
            self.console(b'a', b'PROFILE HERMES RX_ONLY=0')

    def _read(self, size):
        data = bytearray()
        deadline = time.monotonic() + 1.5
        while len(data) < size and time.monotonic() < deadline:
            data.extend(self.port.read(size - len(data)))
        if len(data) != size:
            raise RadioCommunicationError('Short radio reply: {} of {} bytes'.format(len(data), size))
        return bytes(data)

    def _ack(self):
        reply = self._read(1)
        if reply != b'\x06':
            raise RadioCommunicationError('Radio did not acknowledge the command: expected 06, received {}'.format(reply.hex()))

    @contextmanager
    def programming(self):
        self.port.reset_input_buffer()
        self.port.write(b'q')
        deadline = time.monotonic() + 20
        retry = None
        while time.monotonic() < deadline:
            if retry is not None and time.monotonic() >= retry:
                self.port.write(b'q')
                retry = None
            line = self.port.readline()
            if line.startswith(b'CHIRP READY '):
                break
            if line.startswith(b'CHIRP BUSY'):
                retry = time.monotonic() + .5
            if line.startswith(b'CHIRP ERROR'):
                raise RuntimeError(line.decode().strip())
        else:
            raise RuntimeError('C3 programming bridge unavailable')
        try:
            self.port.write(b'\x02')
            time.sleep(.1)
            self.port.write(b'PROGRAM')
            self._ack()
            self.port.write(b'\x02')
            ident = self._read(8)
            if b'P3107' not in ident:
                raise RuntimeError('Unexpected radio identity: {}'.format(ident.hex()))
            self.port.write(b'\x06')
            self._ack()
            yield self
        finally:
            self.port.write(b'E')
            self.port.flush()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                line = self.port.readline()
                logging.debug('C3 exit %r', line)
                if b'CHIRP CLOSED;' in line:
                    break
            else:
                raise RuntimeError('C3 programming exit not confirmed; TX remains inhibited')

    def read_block(self, address):
        if address not in range(0, MEMORY_SIZE, 8):
            raise ValueError('Invalid memory block')
        command = struct.pack('>cHB', b'R', address, 8)
        self.port.write(command)
        reply = self._read(12)
        if reply[:4] != b'W' + command[1:]:
            raise RadioCommunicationError('Bad memory reply at {:04x}'.format(address))
        self.port.write(b'\x06')
        self._ack()
        return reply[4:]

    def read_image(self):
        return b''.join(self.read_block(address) for address in range(0, MEMORY_SIZE, 8))

    def write_image(self, image):
        """Upload all CHIRP-supported blocks without interleaving read commands.

        Call this in a fresh programming session, and verify the full image
        in another session after exiting. Only the caller's channel-1 bytes
        differ; other channel/settings data is copied from the Hermes backup.
        """
        if len(image) != MEMORY_SIZE:
            raise ValueError('Radio image must contain exactly 992 bytes')
        for start, end in UPLOAD_RANGES:
            for address in range(start, end, 8):
                block = image[address:address + 8]
                self.port.write(struct.pack('>cHB', b'W', address, 8) + block)
                try:
                    self._ack()
                except RadioCommunicationError as error:
                    raise RadioCommunicationError('Upload failed at {:04x}: {}'.format(address, error)) from error


def scanner_channel(hermes_image, frequency):
    """Receive-only NFM channel, no tones, preserving reserved channel bytes."""
    if len(hermes_image) != MEMORY_SIZE or frequency % 10:
        raise ValueError('Invalid profile or frequency')
    data = bytearray(hermes_image[CHANNEL_ONE])
    data[:4] = bytes.fromhex('{:08d}'.format(frequency // 10))[::-1]
    data[4:12] = b'\xff' * 8  # TX inhibited, RX/TX tones absent.
    data[12] = (data[12] & 0xE1) | 0x06  # NFM, no skip, low power, no scramble.
    return bytes(data)
