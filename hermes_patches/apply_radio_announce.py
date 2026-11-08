#!/usr/bin/env python3
"""Speak queued announcements over the radio without an LLM call, and reset idle sessions.

Idempotent: re-run it after `hermes update` (after apply_radio_base.py and
apply_radio_gate.py). Only active when Hermes runs with HERMES_RADIO_GATED=1;
HERMES_RADIO_ANNOUNCE=0 turns announcements off without unpatching.

Any program can drop a JSON file into ~/.local/state/baofeng-radio/announce/:

    {"kind": "announce", "text": "It is ten o'clock.", "at": "2026-11-06T13:00:00+00:00"}

(examples/announcer_stub/announcer.py is a minimal producer.) A thread in the
Hermes CLI takes the oldest file when the radio is idle and speaks its text
straight through Hermes' TTS: no model call, no tokens. Idle means:
  * the RX gate is closed;
  * nothing is being recorded or transcribed;
  * no turn is running and Hermes' own TTS is done;
  * nothing is queued;
  * all of that has held for HERMES_RADIO_ANNOUNCE_QUIET_S seconds (default 5).
Files older than HERMES_RADIO_ANNOUNCE_MAX_AGE_S (default 600) are dropped unspoken.

The same thread starts a fresh conversation after HERMES_RADIO_IDLE_RESET_MIN
idle minutes (default 20, 0 = off), so a call made in the afternoon does not
carry the whole morning's history.

Usage: python3 apply_radio_announce.py [~/.hermes/hermes-agent]
"""

import py_compile
import shutil
import sys
from pathlib import Path

MARK = "# baofeng-radio-announce v1"

VOICE_METHODS = '''
    # baofeng-radio-announce v1: speak spooled announcements directly through TTS.
    def _radio_announce_idle(self, gate_path) -> bool:
        tts_done = getattr(self, "_voice_tts_done", None)
        if (not getattr(self, "_voice_mode", False)
                or getattr(self, "_agent_running", False)
                or getattr(self, "_voice_recording", False)
                or getattr(self, "_voice_processing", False)
                or (tts_done is not None and not tts_done.is_set())
                or not self._pending_input.empty()):
            return False
        try:
            with open(gate_path, encoding="utf-8") as gate_file:
                return not gate_file.read().startswith("open")
        except OSError:
            return True

    def _radio_announce_watcher(self):
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        from cli import _DIM, _RST, _cprint
        spool = os.path.expanduser(os.environ.get(
            "HERMES_RADIO_ANNOUNCE_DIR", "~/.local/state/baofeng-radio/announce"))
        gate = os.path.expanduser(os.environ.get(
            "HERMES_RADIO_GATE_FILE", "~/.local/state/baofeng-radio/rx_gate"))
        quiet_s = float(os.environ.get("HERMES_RADIO_ANNOUNCE_QUIET_S", "5"))
        max_age_s = float(os.environ.get("HERMES_RADIO_ANNOUNCE_MAX_AGE_S", "600"))
        reset_s = float(os.environ.get("HERMES_RADIO_IDLE_RESET_MIN", "20")) * 60
        announce = os.environ.get("HERMES_RADIO_ANNOUNCE", "1") != "0"
        idle_since = None
        talked = False      # a turn ran since the last reset
        while not getattr(self, "_should_exit", False):
            time.sleep(2)
            if getattr(self, "_agent_running", False):
                talked = True
            if not self._radio_announce_idle(gate):
                idle_since = None
                continue
            now = time.monotonic()
            idle_since = idle_since or now
            try:
                names = sorted(n for n in os.listdir(spool) if n.endswith(".json") and not n.startswith(".")) \\
                    if announce else []
            except OSError:
                names = []
            if not names:
                if (reset_s > 0 and talked and now - idle_since >= reset_s
                        and not getattr(self, "_radio_context_reset_queued", False)):
                    from hermes_cli.cli_tui_runtime_mixin import _RADIO_CONTEXT_RESET
                    talked = False
                    self._radio_context_reset_queued = True
                    self._pending_input.put(_RADIO_CONTEXT_RESET)
                    _radio_dashboard_event("context_reset", f"Context cleared after {int(reset_s // 60)} idle minutes.")
                continue
            if now - idle_since < quiet_s:
                continue
            path = os.path.join(spool, names[0])
            try:
                with open(path, encoding="utf-8") as spool_file:
                    item = _json.load(spool_file)
            except (OSError, ValueError):
                item = {}
            try:
                os.unlink(path)
            except OSError:
                pass
            if not isinstance(item, dict):
                continue
            try:
                at = _dt.fromisoformat(str(item.get("at")))
                age = (_dt.now(_tz.utc) - at).total_seconds()
            except (TypeError, ValueError):
                age = 0
            text = str(item.get("text") or "").strip()
            if not text or age > max_age_s:
                _cprint(f"{_DIM}radio: announcement dropped ({int(age)}s old){_RST}")
                continue
            if not getattr(self, "_voice_tts", False):
                _cprint(f"{_DIM}radio: announcement not spoken, TTS is off{_RST}")
                continue
            _cprint(f"{_DIM}radio: {text[:120]}{_RST}")
            try:
                self._voice_speak_response_async(text)
                _radio_dashboard_event("announce", text)
            except Exception as error:
                _cprint(f"{_DIM}radio: announcement failed: {error}{_RST}")
            idle_since = None
'''

VOICE_ANCHOR = "    def _voice_beep(self, *, frequency: int, count: int) -> None:\n"

SIGNAL_ANCHOR = (
    "            if hasattr(_signal, 'SIGUSR1'):\n"
    "                _signal.signal(_signal.SIGUSR1, self._tui_queue_radio_context_reset)\n"
)
SIGNAL_PATCH = (
    SIGNAL_ANCHOR
    + "            if os.environ.get(\"HERMES_RADIO_GATED\"):  "
    + MARK + "\n"
    + "                threading.Thread(target=self._radio_announce_watcher, daemon=True,\n"
    + "                                 name=\"radio-announce\").start()\n"
)


def patch(path: Path, edits) -> None:
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        print(f"already patched: {path}")
        return
    for anchor, replacement in edits:
        if text.count(anchor) != 1:
            sys.exit(f"anchor not found exactly once in {path} (apply apply_radio_base.py first?):\n{anchor}")
        text = text.replace(anchor, replacement)
    compile(text, str(path), "exec")
    backup = path.with_name(path.name + ".pre-radio-announce")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as error:
        shutil.copy2(backup, path)
        sys.exit(f"patched file did not compile, restored backup: {error}")
    print(f"patched: {path}")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/hermes-agent").expanduser()
    voice = root / "hermes_cli" / "cli_voice_mixin.py"
    runtime = root / "hermes_cli" / "cli_tui_runtime_mixin.py"
    for path, modules in ((voice, ("os", "time")), (runtime, ("os", "threading"))):
        text = path.read_text(encoding="utf-8")
        for module in modules:
            if f"\nimport {module}\n" not in text:
                sys.exit(f"{path} no longer imports {module}; update this patch")
    patch(voice, [(VOICE_ANCHOR, VOICE_METHODS.lstrip("\n") + "\n" + VOICE_ANCHOR)])
    patch(runtime, [(SIGNAL_ANCHOR, SIGNAL_PATCH)])


if __name__ == "__main__":
    main()
