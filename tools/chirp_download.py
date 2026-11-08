#!/usr/bin/env python3
"""Read a BF-888/BF-777S through the C3's on-demand programming bridge.

Run with the Python environment containing CHIRP and pyserial. This helper
allows only identification, read-block, acknowledgement and exit commands.
"""

import argparse
import builtins
import csv
from datetime import datetime
import logging
from pathlib import Path
import sys
import struct
import time

import serial

class ReadOnlyPipe:
    def __init__(self, port, slow_handshake=False):
        self.port = port
        self.slow_handshake = slow_handshake

    @property
    def timeout(self):
        return self.port.timeout

    @timeout.setter
    def timeout(self, value):
        self.port.timeout = value

    def write(self, data):
        if data not in (b'\x02', b'PROGRAM', b'\x06', b'E'):
            if not (len(data) == 4 and data[:1] == b'R'
                    and data[3] == 8
                    and int.from_bytes(data[1:3], 'big') in range(0, 0x3E0, 8)):
                raise ValueError('Blocked command outside the download protocol')
        logging.debug('TX %s', data.hex())
        if data == b'PROGRAM' and self.slow_handshake:
            for byte in data:
                self.port.write(bytes([byte]))
                self.port.flush()
                time.sleep(0.01)
            return len(data)
        return self.port.write(data)

    def read(self, count):
        data = self.port.read(count)
        logging.debug('RX %s', data.hex())
        return data

    def log(self, message):
        logging.debug('%s', message)


def raw_download(pipe, prefix):
    """Independent BF-888 read protocol; no CHIRP imports or driver calls."""
    pipe.timeout = 2
    pipe.write(b'\x02')
    time.sleep(0.1)
    pipe.write(b'PROGRAM')
    reply = pipe.read(1)
    print('Raw handshake reply:', reply.hex(' ') or '(no bytes)', flush=True)
    if reply != b'\x06':
        raise RuntimeError('Raw handshake did not return ACK (06)')
    pipe.write(b'\x02')
    ident = pipe.read(8)
    print('Raw radio ID:', ident.hex(' '), repr(ident), flush=True)
    if len(ident) != 8 or b'P3107' not in ident:
        raise RuntimeError('Radio ID does not match the expected BF-888 protocol')
    pipe.write(b'\x06')
    if pipe.read(1) != b'\x06':
        raise RuntimeError('Missing ACK after identification')
    data = bytearray()
    for address in range(0, 0x3E0, 8):
        command = struct.pack('>cHB', b'R', address, 8)
        pipe.write(command)
        reply = pipe.read(12)
        if len(reply) != 12 or reply[:4] != b'W' + command[1:]:
            raise RuntimeError('Invalid raw block reply at {:04x}: {}'.format(
                address, reply.hex(' ')))
        data.extend(reply[4:])
        pipe.write(b'\x06')
        if pipe.read(1) != b'\x06':
            raise RuntimeError('Missing raw block ACK at {:04x}'.format(address))
    pipe.write(b'E')
    with open(str(prefix) + '.bin', 'xb') as stream:
        stream.write(data)
    print('Saved {} raw bytes: {}.bin'.format(len(data), prefix), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyUSB0')
    parser.add_argument('--slow-handshake', action='store_true',
                        help='space the PROGRAM bytes by 10 ms')
    parser.add_argument('--raw', action='store_true',
                        help='download directly without the CHIRP driver; save raw .bin')
    parser.add_argument('--output-dir', type=Path, default=Path('diagnostics/chirp'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_dir / datetime.now().strftime('radio-download-%Y%m%d-%H%M%S-%f')
    logging.basicConfig(filename=str(prefix) + '.log', level=logging.DEBUG,
                        format='%(asctime)s %(levelname)s %(message)s')
    port = serial.Serial(baudrate=115200, timeout=0.5, exclusive=True)
    port.dtr = False
    port.rts = False
    port.port = args.port
    entered = False
    try:
        port.open()
        port.reset_input_buffer()
        port.write(b'b')
        deadline = time.monotonic() + 60
        retry_at = None
        while time.monotonic() < deadline:
            if retry_at is not None and time.monotonic() >= retry_at:
                port.write(b'b')
                retry_at = None
            line = port.readline()
            logging.debug('CONSOLE %r', line)
            # CH340 boards can reset on open; the first request can arrive
            # before Serial.begin(). Retry once startup advertises the mode.
            if b'  b - 9600-baud radio programming bridge' in line:
                port.write(b'b')
            if line.startswith(b'CHIRP READY '):
                entered = True
                print(line.decode().strip(), flush=True)
                break
            if line.startswith(b'CHIRP BUSY'):
                retry_at = time.monotonic() + 2
            if line.startswith(b'CHIRP ERROR'):
                raise RuntimeError(line.decode().strip())
        if not entered:
            raise RuntimeError('C3 did not enter programming mode; see log')

        pipe = ReadOnlyPipe(port, args.slow_handshake)
        if args.raw:
            raw_download(pipe, prefix)
            return
        builtins._ = lambda text: text
        from chirp.drivers.h777 import H777Radio
        radio = H777Radio(pipe)
        radio.status_fn = lambda status: None
        radio.sync_in()
        radio.save(str(prefix) + '.img')
        with open(str(prefix) + '.bin', 'xb') as stream:
            stream.write(radio.get_mmap().get_packed())
        with open(str(prefix) + '.csv', 'x', newline='') as stream:
            writer = csv.writer(stream)
            from chirp import chirp_common
            writer.writerow(chirp_common.Memory.CSV_FORMAT)
            for number in range(1, 17):
                memory = radio.get_memory(number)
                if not memory.empty:
                    writer.writerow(memory.to_csv())
                    print(memory)
        print('Saved:', str(prefix) + '.img', flush=True)
    finally:
        if port.is_open:
            if entered:
                port.write(b'E')
                port.flush()
                # Let firmware relinquish UART/PT and resume normal audio.
                deadline = time.monotonic() + 18
                while time.monotonic() < deadline:
                    line = port.readline()
                    logging.debug('CLOSE %r', line)
                    if b'CHIRP TRACE' in line:
                        summary = line.decode(errors='replace').strip().split(' data=', 1)[0]
                        print(summary, flush=True)
                    if b'CHIRP CLOSED' in line:
                        print('Audio resumed; PTT OFF.', flush=True)
                        break
                else:
                    print('Bridge close not confirmed; check C3 serial console.', flush=True)
            port.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        logging.exception('Download failed')
        print('Download failed: {}'.format(error), file=sys.stderr)
        sys.exit(1)
