#!/usr/bin/env python3
"""Local faster-whisper transcription for completed BF-777S utterance WAVs.

First install the project-local dependency environment:
    python3 -m baofeng_bridge.stt.stt --install

Then keep this running in a second terminal while bridge.py writes WAVs:
    python3 -m baofeng_bridge.stt.stt --watch utterances --language en
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

from baofeng_bridge.paths import PROJECT_ROOT as PROJECT_DIR

VENV_DIR = PROJECT_DIR / ".stt-venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"


def install_dependencies():
    """Create an isolated venv so Debian's externally managed Python is untouched."""
    if not VENV_PYTHON.exists():
        try:
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        except subprocess.CalledProcessError as error:
            raise SystemExit(
                "Could not create .stt-venv. Install python3-venv, then retry: "
                "sudo apt install python3-venv"
            ) from error
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "faster-whisper"], check=True)
    print("Installed faster-whisper in .stt-venv.")
    print("Next: python3 -m baofeng_bridge.stt.stt --watch utterances --language en")


def relaunch_in_venv_if_available():
    if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), "-m", "baofeng_bridge.stt.stt", *sys.argv[1:]])


class Transcriber:
    def __init__(
        self,
        model_name,
        device,
        compute_type,
        language,
        use_vad,
        vad_silence_ms,
        initial_prompt,
    ):
        try:
            from faster_whisper import WhisperModel
        except ModuleNotFoundError as error:
            raise SystemExit(
                "faster-whisper is not installed. Run: python3 -m baofeng_bridge.stt.stt --install"
            ) from error

        self.language = None if language == "auto" else language
        self.use_vad = use_vad
        self.vad_parameters = {
            "min_silence_duration_ms": vad_silence_ms,
            "speech_pad_ms": 200,
        }
        self.initial_prompt = initial_prompt or None
        print(
            "Loading faster-whisper model={} device={} compute_type={}...".format(
                model_name, device, compute_type
            ),
            flush=True,
        )
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        print("STT ready.", flush=True)

    def transcribe(self, wav_path):
        options = {
            "language": self.language,
            "vad_filter": self.use_vad,
            "beam_size": 5,
            "temperature": 0.0,
            # Radio packets can have noise bursts between words. Do not let a
            # previous noisy chunk bias decoding of the next chunk.
            "condition_on_previous_text": False,
            "initial_prompt": self.initial_prompt,
        }
        if self.use_vad:
            options["vad_parameters"] = self.vad_parameters
        segments, info = self.model.transcribe(str(wav_path), **options)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        detected_language = getattr(info, "language", self.language or "unknown")
        probability = getattr(info, "language_probability", 0.0)
        return text, detected_language, probability


def transcript_path_for(wav_path):
    return wav_path.with_suffix(".txt")


def write_transcript(wav_path, text, language, probability):
    output_path = transcript_path_for(wav_path)
    temporary_path = output_path.with_suffix(".partial")
    temporary_path.write_text(text + "\n", encoding="utf-8")
    temporary_path.replace(output_path)
    print(
        "TRANSCRIPT wav={} language={} probability={:.2f} text={!r}".format(
            wav_path, language, probability, text
        ),
        flush=True,
    )


def process_wav(transcriber, wav_path, force):
    output_path = transcript_path_for(wav_path)
    if output_path.exists() and not force:
        return False
    print("STT_TRANSCRIBING wav={}".format(wav_path), flush=True)
    text, language, probability = transcriber.transcribe(wav_path)
    write_transcript(wav_path, text, language, probability)
    return True


def main():
    if "--install" in sys.argv:
        if len(sys.argv) != 2:
            raise SystemExit("--install is used alone: python3 -m baofeng_bridge.stt.stt --install")
        install_dependencies()
        return

    relaunch_in_venv_if_available()

    parser = argparse.ArgumentParser(description="Transcribe BF-777S utterance WAVs locally.")
    parser.add_argument("wavs", nargs="*", type=Path, help="one or more completed utterance WAV files")
    parser.add_argument("--watch", metavar="DIRECTORY", type=Path,
                        help="continuously transcribe new .wav files in DIRECTORY")
    parser.add_argument(
        "--model",
        default="small",
        help="multilingual faster-whisper model (default: small; use base for a faster smoke test)",
    )
    parser.add_argument("--language", default=os.environ.get("BAOFENG_LANG", "en").split("-")[0].lower(),
                        help="language code, or auto for detection (default: BAOFENG_LANG, else en)")
    parser.add_argument("--device", default="cpu", choices=("auto", "cpu", "cuda"),
                        help="model device (default: cpu; use cuda only with CUDA 12 installed)")
    parser.add_argument("--compute-type", default="int8",
                        help="faster-whisper compute type (default: int8)")
    parser.add_argument("--no-vad-filter", action="store_true",
                        help="disable faster-whisper's internal speech/silence filter")
    parser.add_argument(
        "--vad-silence-ms",
        type=int,
        default=500,
        help="silence needed for the internal VAD to split speech (default: 500)",
    )
    parser.add_argument(
        "--initial-prompt",
        default="Verbatim transcript of a two-way radio conversation.",
        help="short decoding hint; pass an empty string to disable it",
    )
    parser.add_argument("--poll-ms", type=int, default=250,
                        help="watch polling interval in ms (default: 250)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing .txt transcripts")
    args = parser.parse_args()

    if not args.wavs and args.watch is None:
        parser.error("provide WAV paths or --watch DIRECTORY")
    if args.poll_ms < 50:
        parser.error("--poll-ms must be at least 50")
    if args.vad_silence_ms < 100:
        parser.error("--vad-silence-ms must be at least 100")
    if args.watch:
        args.watch.mkdir(parents=True, exist_ok=True)

    transcriber = Transcriber(
        args.model,
        args.device,
        args.compute_type,
        args.language,
        not args.no_vad_filter,
        args.vad_silence_ms,
        args.initial_prompt,
    )

    for wav_path in args.wavs:
        if not wav_path.is_file():
            print("STT_SKIP missing={}".format(wav_path), file=sys.stderr, flush=True)
            continue
        process_wav(transcriber, wav_path, args.force)

    if args.watch is None:
        return

    print("Watching {} for completed .wav utterances...".format(args.watch), flush=True)
    try:
        while True:
            for wav_path in sorted(args.watch.glob("*.wav")):
                process_wav(transcriber, wav_path, args.force)
            time.sleep(args.poll_ms / 1000.0)
    except KeyboardInterrupt:
        print("\nSTT watcher stopped.")


if __name__ == "__main__":
    main()
