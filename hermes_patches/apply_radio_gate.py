#!/usr/bin/env python3
"""Apply the Baofeng RX-gated recording patch to a Hermes Agent checkout.

Idempotent: re-run it after `hermes update` overwrites the local changes.
Only active when Hermes is started with HERMES_RADIO_GATED=1, so normal
interactive use (Ctrl+B, continuous mode) is unchanged.

radio_rx.py writes "<open|closed> <seq> <loud_ms>" to the gate file whenever its
RF squelch gate opens or closes. Hermes then:
  * enables voice mode on its own at startup (no /voice on, no Ctrl+B);
  * starts recording when the gate opens (ignored while Hermes' own TTS plays);
  * stops and transcribes as soon as the gate closes, instead of waiting for
    3 s of silence;
  * cancels bursts with less than HERMES_RADIO_MIN_UTTERANCE_S of real audio
    (touch/wiring noise), so they never reach the STT API.

Usage: python3 apply_radio_gate.py [~/.hermes/hermes-agent]
"""

import shutil
import sys
from pathlib import Path

MARK = "# baofeng-radio-gate"

VOICE_METHODS = '''
    # baofeng-radio-gate: RX-gated recording driven by radio_rx.py's squelch file.
    def _radio_gate_watcher(self):
        from cli import _DIM, _RST, _cprint
        path = os.path.expanduser(os.environ.get(
            "HERMES_RADIO_GATE_FILE", "~/.local/state/baofeng-radio/rx_gate"))
        min_s = float(os.environ.get("HERMES_RADIO_MIN_UTTERANCE_S", "0.8"))
        time.sleep(float(os.environ.get("HERMES_RADIO_STARTUP_DELAY_S", "3")))
        if not getattr(self, "_voice_mode", False):
            try:
                self._enable_voice_mode()
            except Exception as e:
                _cprint(f"{_DIM}radio gate: voice mode failed to start: {e}{_RST}")

        def _read():
            try:
                with open(path, encoding="utf-8") as gate_file:
                    return gate_file.read().strip()
            except OSError:
                return ""

        last = _read()  # ignore whatever state was left before startup
        recording_from_gate = False
        _cprint(f"{_DIM}radio gate: waiting for RX ({path}){_RST}")
        while not getattr(self, "_should_exit", False):
            state = _read()
            if state and state != last:
                last = state
                parts = state.split()
                if parts[0] == "open":
                    recording_from_gate = self._radio_gate_open()
                elif parts[0] == "closed" and recording_from_gate:
                    recording_from_gate = False
                    loud_s = (float(parts[2]) / 1000.0) if len(parts) > 2 else min_s
                    self._radio_gate_close(loud_s, min_s)
            time.sleep(0.05)

    def _radio_gate_open(self) -> bool:
        from cli import _DIM, _RST, _cprint
        if not getattr(self, "_voice_mode", False):
            # A spoken "stop" disables voice mode; on the radio that would leave
            # Hermes deaf, so the next transmission turns it back on.
            try:
                self._enable_voice_mode()
            except Exception:
                return False
            if not getattr(self, "_voice_mode", False):
                return False
        with self._voice_lock:
            if self._voice_recording or getattr(self, "_voice_processing", False):
                return False
        tts_done = getattr(self, "_voice_tts_done", None)
        if tts_done is not None and not tts_done.is_set():
            return False  # our own transmission is playing
        try:
            self._voice_start_recording()
            self._voice_invalidate()
            return True
        except Exception as e:
            _cprint(f"{_DIM}radio gate: recording failed: {e}{_RST}")
            return False

    def _radio_gate_close(self, loud_s: float, min_s: float) -> None:
        from cli import _DIM, _RST, _cprint
        with self._voice_lock:
            if not self._voice_recording:
                return  # already stopped (manual Ctrl+B or recorder cap)
        if loud_s < min_s:
            with self._voice_lock:
                self._voice_recording = False
                recorder = self._voice_recorder
            if recorder is not None:
                try:
                    recorder.cancel()
                except Exception:
                    pass
            _cprint(f"{_DIM}radio: short RX ({loud_s:.1f}s) ignored, no STT{_RST}")
            self._voice_invalidate()
            return
        threading.Thread(target=self._voice_stop_and_transcribe, daemon=True).start()
'''

SILENCE_ANCHOR = '        rec._silence_duration = _numeric_or(voice_cfg.get("silence_duration"), 3.0)\n'
SILENCE_PATCH = (
    SILENCE_ANCHOR
    + "        if os.environ.get(\"HERMES_RADIO_GATED\"):  " + MARK + "\n"
    + "            # The radio squelch decides when the utterance ends, not a silence timer.\n"
    + "            rec._silence_duration = max(rec._silence_duration, 60.0)\n"
)

VOICE_ANCHOR = "    def _voice_beep(self, *, frequency: int, count: int) -> None:\n"

SIGNAL_ANCHOR = (
    "            if hasattr(_signal, 'SIGUSR1'):\n"
    "                _signal.signal(_signal.SIGUSR1, self._tui_queue_radio_context_reset)\n"
)
SIGNAL_PATCH = (
    SIGNAL_ANCHOR
    + "            if os.environ.get(\"HERMES_RADIO_GATED\"):  " + MARK + "\n"
    + "                threading.Thread(target=self._radio_gate_watcher, daemon=True,\n"
    + "                                 name=\"radio-gate\").start()\n"
)


def patch(path: Path, edits) -> None:
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        print(f"already patched: {path}")
        return
    for anchor, replacement in edits:
        if text.count(anchor) != 1:
            sys.exit(f"anchor not found exactly once in {path}:\n{anchor}")
        text = text.replace(anchor, replacement)
    backup = path.with_name(path.name + ".pre-radio-gate")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")
    print(f"patched: {path}")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/hermes-agent").expanduser()
    voice = root / "hermes_cli" / "cli_voice_mixin.py"
    runtime = root / "hermes_cli" / "cli_tui_runtime_mixin.py"
    patch(voice, [(SILENCE_ANCHOR, SILENCE_PATCH),
                  (VOICE_ANCHOR, VOICE_METHODS.lstrip("\n") + "\n" + VOICE_ANCHOR)])
    runtime_text = runtime.read_text(encoding="utf-8")
    for module in ("os", "threading"):
        if f"\nimport {module}\n" not in runtime_text:
            sys.exit(f"{runtime} no longer imports {module}; update this patch")
    patch(runtime, [(SIGNAL_ANCHOR, SIGNAL_PATCH)])


if __name__ == "__main__":
    main()
