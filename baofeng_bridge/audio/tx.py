#!/usr/bin/env python3
"""Send Chatterbox-cloned speech to the BF-777S ESP32 TX endpoint.

The local Chatterbox server returns a complete WAV response, so this client
must hold that response long enough to convert it to 16 kHz signed PCM. It
then streams 20 ms frames in real time; it does not play any audio locally.

Other code can import ``synthesize_and_transmit`` or
``send_pcm_stream`` instead of spawning this command.
"""

import argparse
import warnings
warnings.filterwarnings("ignore", message="'audioop' is deprecated", category=DeprecationWarning)
import audioop
from contextlib import closing
import io
import mimetypes
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

from baofeng_bridge.paths import PROJECT_ROOT as PROJECT_DIR


SAMPLE_RATE = 16000
SAMPLES_PER_PACKET = 320
PCM_FRAME_BYTES = SAMPLES_PER_PACKET * 2
FRAME_DURATION_S = SAMPLES_PER_PACKET / SAMPLE_RATE
TX_UDP_PORT = 7778

TX_MAGIC = 0x58544642  # Little-endian bytes: BFTX
TX_BEGIN = 1
TX_AUDIO = 2
TX_END = 3
TX_HEADER = struct.Struct("<IBBHH")

# Must match PTT_LEAD_MS in firmware. TX_BEGIN keys the radio first; waiting
# here avoids filling the ESP32's small jitter buffer before DAC playback.
PTT_LEAD_MS = 1500
BEGIN_RETRY_INTERVAL_S = 0.05

# Your own voice-clone reference (a few seconds of clean speech). voices/ is git-ignored.
DEFAULT_VOICE = PROJECT_DIR / "voices" / "reference.mp3"


def _multipart_clone_body(text, voice_path, language):
    """Build the simple multipart request accepted by Chatterbox /clone."""
    boundary = "----baofeng-{}".format(uuid.uuid4().hex)
    boundary_bytes = boundary.encode("ascii")
    body = bytearray()

    def add_field(name, value):
        body.extend(b"--" + boundary_bytes + b"\r\n")
        body.extend(
            'Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name).encode("utf-8")
        )
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    add_field("input", text)
    if language:
        add_field("language", language)

    mime_type = mimetypes.guess_type(voice_path.name)[0] or "application/octet-stream"
    body.extend(b"--" + boundary_bytes + b"\r\n")
    body.extend(
        (
            'Content-Disposition: form-data; name="voice"; filename="{}"\r\n'
            "Content-Type: {}\r\n\r\n"
        ).format(voice_path.name, mime_type).encode("utf-8")
    )
    body.extend(voice_path.read_bytes())
    body.extend(b"\r\n--" + boundary_bytes + b"--\r\n")
    return bytes(body), boundary


def chatterbox_clone(chatterbox_url, text, voice_path, language, timeout_s):
    """Return a cloned-speech WAV from the local Chatterbox server."""
    voice_path = Path(voice_path)
    if not voice_path.is_file():
        raise ValueError("voice reference does not exist: {}".format(voice_path))
    body, boundary = _multipart_clone_body(text, voice_path, language)
    endpoint = chatterbox_url.rstrip("/") + "/v1/audio/speech/clone"
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary={}".format(boundary)},
        method="POST",
    )
    try:
        with closing(urllib.request.urlopen(request, timeout=timeout_s)) as response:
            content_type = response.headers.get_content_type()
            wav = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError("Chatterbox returned HTTP {}: {}".format(error.code, detail)) from error
    except urllib.error.URLError as error:
        raise RuntimeError("cannot contact Chatterbox at {}: {}".format(endpoint, error.reason)) from error

    if content_type not in ("audio/wav", "audio/x-wav", "application/octet-stream"):
        raise RuntimeError("Chatterbox returned {}, not WAV audio".format(content_type))
    if not wav:
        raise RuntimeError("Chatterbox returned an empty audio response")
    return wav


def wav_to_radio_pcm(wav_bytes):
    """Convert PCM WAV audio to 16 kHz, mono, signed-16-bit little-endian PCM."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            compression = source.getcomptype()
            pcm = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as error:
        raise ValueError("Chatterbox response is not a readable WAV file: {}".format(error)) from error

    if compression != "NONE":
        raise ValueError("WAV must contain uncompressed PCM, got {}".format(compression))
    if channels not in (1, 2) or sample_width not in (1, 2, 3, 4) or sample_rate < 8000:
        raise ValueError(
            "unsupported WAV format: {} channel(s), {}-byte samples at {} Hz".format(
                channels, sample_width, sample_rate
            )
        )

    # WAV's 8-bit PCM is unsigned, while audioop operates on signed samples.
    if sample_width == 1:
        pcm = audioop.bias(pcm, 1, -128)
    if channels == 2:
        pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
    if sample_width != 2:
        pcm = audioop.lin2lin(pcm, sample_width, 2)
    if sample_rate != SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, 2, 1, sample_rate, SAMPLE_RATE, None)
    if not pcm:
        raise ValueError("WAV contains no PCM samples after conversion")
    return pcm


def _project_ffmpeg_executable():
    """Return the decoder installed in the project-local virtual environment."""
    helper = PROJECT_DIR / ".build-venv" / "bin" / "python"
    if not helper.is_file():
        raise RuntimeError("project audio decoder is unavailable; run the project setup first")
    lookup = subprocess.run(
        [
            str(helper),
            "-c",
            "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    candidate = Path(lookup.stdout.strip())
    if lookup.returncode == 0 and candidate.is_file():
        return str(candidate)
    detail = lookup.stderr.strip() or "imageio-ffmpeg did not provide an executable"
    raise RuntimeError("project audio decoder is unavailable: {}".format(detail))


def read_audio_as_radio_pcm(path):
    """Decode a local WAV or MP3 file to 16 kHz mono signed-16-bit PCM."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("audio file does not exist: {}".format(path))
    if path.suffix.lower() == ".wav":
        return wav_to_radio_pcm(path.read_bytes())

    result = subprocess.run(
        [
            _project_ffmpeg_executable(),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("audio decode failed: {}".format(detail or "unknown FFmpeg error"))
    if not result.stdout:
        raise ValueError("audio file produced no PCM samples: {}".format(path))
    return result.stdout


class TxUdpStream:
    """Paced BFTX datagram stream with monotonically wrapping sequence numbers."""

    def __init__(self, host, port=TX_UDP_PORT, begin_repeats=1):
        if begin_repeats < 1:
            raise ValueError("begin_repeats must be at least 1")
        self.destination = (host, port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sequence = 0
        self.begin_repeats = begin_repeats
        self.started = False

    def close(self):
        self.socket.close()

    def _send(self, packet_type, payload=b"", sequence=None):
        if len(payload) > 0xFFFF:
            raise ValueError("TX payload is too large")
        advances_sequence = sequence is None
        if sequence is None:
            sequence = self.sequence
        header = TX_HEADER.pack(TX_MAGIC, packet_type, 0, sequence, len(payload))
        self.socket.sendto(header + payload, self.destination)
        if advances_sequence:
            self.sequence = (self.sequence + 1) & 0xFFFF

    def begin(self):
        if self.started:
            raise RuntimeError("TX stream already started")
        begin_sequence = self.sequence
        self._send(TX_BEGIN)
        # ESP32-C3 firmware accepts duplicate BEGINs with this same sequence.
        # This optional retry closes the one-lost-control-datagram failure mode
        # without changing normal audio-frame sequence numbering.
        for _ in range(self.begin_repeats - 1):
            time.sleep(BEGIN_RETRY_INTERVAL_S)
            self._send(TX_BEGIN, sequence=begin_sequence)
        self.started = True
        # Do not send PCM yet: the ESP32 has keyed PTT but deliberately waits
        # through the same lead interval before it starts draining its buffer.
        # Sending in that interval would overflow the 480 ms packet queue.
        if PTT_LEAD_MS:
            time.sleep(PTT_LEAD_MS / 1000.0)

    def send_pcm(self, pcm):
        if not self.started:
            raise RuntimeError("call begin before sending PCM")
        if len(pcm) % 2:
            raise ValueError("PCM must be signed 16-bit samples")

        frames = (len(pcm) + PCM_FRAME_BYTES - 1) // PCM_FRAME_BYTES
        started_at = time.monotonic()
        for frame_index in range(frames):
            start = frame_index * PCM_FRAME_BYTES
            payload = pcm[start:start + PCM_FRAME_BYTES]
            if len(payload) < PCM_FRAME_BYTES:
                payload += b"\x00" * (PCM_FRAME_BYTES - len(payload))
            self._send(TX_AUDIO, payload)
            deadline = started_at + (frame_index + 1) * FRAME_DURATION_S
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def end(self):
        if self.started:
            self._send(TX_END)
            self.started = False


def send_pcm_stream(host, pcm, port=TX_UDP_PORT, begin_repeats=1):
    """Stream already-converted 16 kHz signed PCM to an ESP32 TX endpoint."""
    stream = TxUdpStream(host, port, begin_repeats=begin_repeats)
    try:
        stream.begin()
        stream.send_pcm(pcm)
        stream.end()
    finally:
        # An interrupted sender deliberately relies on the ESP32's TX_TIMEOUT_MS
        # watchdog to release PTT. Send TX_END when the socket remains usable.
        try:
            stream.end()
        finally:
            stream.close()


def synthesize_and_transmit(host, text, *, port=TX_UDP_PORT,
                            chatterbox_url="http://127.0.0.1:8000",
                            voice_path=DEFAULT_VOICE, language="pt", timeout_s=180,
                            begin_repeats=1):
    """Chatterbox clone synthesis followed by the normal paced ESP32 TX stream."""
    wav = chatterbox_clone(chatterbox_url, text, voice_path, language, timeout_s)
    pcm = wav_to_radio_pcm(wav)
    send_pcm_stream(host, pcm, port, begin_repeats=begin_repeats)
    return len(pcm) // 2


def main():
    parser = argparse.ArgumentParser(description="Transmit Chatterbox speech through the BF-777S.")
    parser.add_argument("text", nargs="?", help="text to synthesize and transmit")
    parser.add_argument("--test", action="store_true", help="stream the original voice file directly; no Chatterbox")
    parser.add_argument("--esp32-host", required=True, help="ESP32 LAN address shown on its serial console")
    parser.add_argument("--tx-port", type=int, default=TX_UDP_PORT, help="ESP32 TX UDP port (default: 7778)")
    parser.add_argument("--begin-repeats", type=int, default=1,
                        help="repeat TX_BEGIN with its original sequence (use 3 for C3; default: 1)")
    parser.add_argument("--chatterbox-url", default="http://127.0.0.1:8000",
                        help="local Chatterbox base URL")
    parser.add_argument("--voice", type=Path, default=DEFAULT_VOICE,
                        help="voice-clone reference and direct-test source audio")
    parser.add_argument("--language", default="pt", help="Chatterbox language (default: pt)")
    parser.add_argument("--timeout", type=float, default=180,
                        help="Chatterbox request timeout in seconds (default: 180)")
    parser.add_argument("--wav", type=Path,
                        help="send a local WAV or MP3 through TX instead of invoking Chatterbox")
    args = parser.parse_args()

    if args.tx_port < 1 or args.tx_port > 65535 or args.timeout <= 0:
        parser.error("--tx-port must be 1..65535 and --timeout must be positive")
    if args.begin_repeats < 1 or args.begin_repeats > 5:
        parser.error("--begin-repeats must be 1..5")
    if args.wav and (args.text or args.test):
        parser.error("--wav is a direct-file mode; do not combine it with text or --test")
    if args.test and args.text:
        parser.error("--test streams the original voice file; do not combine it with text")
    if args.test:
        args.wav = args.voice
    if not args.wav and not args.text:
        parser.error("provide text, pass --test, or use --wav")

    try:
        if args.wav:
            pcm = read_audio_as_radio_pcm(args.wav)
            print("TX audio file: {} samples -> {}:{} (TX_BEGIN x{})".format(
                len(pcm) // 2, args.esp32_host, args.tx_port, args.begin_repeats
            ))
            send_pcm_stream(args.esp32_host, pcm, args.tx_port, args.begin_repeats)
        else:
            print("Chatterbox clone synthesis using {}...".format(args.voice))
            samples = synthesize_and_transmit(
                args.esp32_host,
                args.text,
                port=args.tx_port,
                chatterbox_url=args.chatterbox_url,
                voice_path=args.voice,
                language=args.language,
                timeout_s=args.timeout,
                begin_repeats=args.begin_repeats,
            )
            print("TX complete: {} samples sent to {}:{}".format(samples, args.esp32_host, args.tx_port))
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit("TX failed: {}".format(error)) from error


if __name__ == "__main__":
    main()
