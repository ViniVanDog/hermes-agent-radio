#!/usr/bin/env python3
"""Add the shared hooks the other radio patches build on. Apply this one FIRST.

Idempotent: re-run it after `hermes update`, then re-run the other patches.

It adds three small things to a Hermes Agent checkout:

  hermes_cli/cli_voice_mixin.py
    * _radio_dashboard_event(kind, text): appends {"ts", "kind", "text"} to the
      radio event log (~/.local/state/baofeng-radio/events.jsonl) that
      dashboard.py and radio_bridge.usage read. It is a no-op unless Hermes
      runs with HERMES_RADIO_GATED set, and it never raises.
    * an "stt" event after each transcript, and a "tts" event before each reply
      is spoken.

  hermes_cli/cli_tui_runtime_mixin.py
    * _RADIO_CONTEXT_RESET: a sentinel put on the REPL input queue to start a
      fresh session. The REPL worker handles it between turns, never mid-turn.
    * a SIGUSR1 handler that queues that sentinel (the dashboard's "new
      conversation" button sends SIGUSR1). It ignores the signal unless
      HERMES_RADIO_GATED is set.

apply_radio_gate.py, apply_radio_input_filter.py, apply_radio_announce.py and
apply_radio_context.py anchor on the lines this patch inserts.

Usage: python3 apply_radio_base.py [~/.hermes/hermes-agent]
"""

import py_compile
import shutil
import sys
from pathlib import Path

MARK = "# baofeng-radio-base v1"

EVENT_FUNCTION = '''
''' + MARK + ''': radio event log shared with dashboard.py and radio_bridge.usage.
def _radio_dashboard_event(kind, text=""):
    """Best-effort; radio handling must never depend on the event log."""
    if not os.environ.get("HERMES_RADIO_GATED"):
        return
    try:
        path = os.path.expanduser(os.environ.get(
            "BAOFENG_EVENTS_FILE", "~/.local/state/baofeng-radio/events.jsonl"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {"ts": time.time(), "kind": str(kind), "text": str(text)[:2000]}
        with open(path, "a", encoding="utf-8") as events:
            events.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\\n")
    except Exception:
        pass

'''

VOICE_EDITS = [
    # Module-level helper, before the first module function after the imports.
    ("\ndef _config_section(name: str) -> dict:\n",
     EVENT_FUNCTION + "\ndef _config_section(name: str) -> dict:\n"),
    # STT event: apply_radio_input_filter.py anchors on this exact line.
    ('                transcript = result["transcript"].strip()\n',
     '                transcript = result["transcript"].strip()\n'
     '                _radio_dashboard_event("stt", transcript)\n'),
    # TTS event: the text that is about to be synthesized.
    ("            self._voice_last_tts_text = tts_text\n",
     "            self._voice_last_tts_text = tts_text\n"
     '            _radio_dashboard_event("tts", tts_text)\n'),
]

RUNTIME_SENTINEL = '''
''' + MARK + ''': queued by SIGUSR1 or the idle reset; handled by the REPL worker.
_RADIO_CONTEXT_RESET = object()

'''

RUNTIME_METHODS = '''    ''' + MARK + '''
    def _tui_queue_radio_context_reset(self, signum=None, frame=None):
        if not os.environ.get("HERMES_RADIO_GATED"):
            return
        self._radio_context_reset_queued = True
        self._pending_input.put(_RADIO_CONTEXT_RESET)

    def _radio_context_reset(self):
        self._radio_context_reset_queued = False
        if getattr(self, "_agent_running", False):
            return
        try:
            self.new_session(silent=True)
        except Exception as error:
            logger.warning("radio context reset failed: %s", error)

'''

RUNTIME_EDITS = [
    ("\nclass CLITuiRuntimeMixin:\n", RUNTIME_SENTINEL + "\nclass CLITuiRuntimeMixin:\n"),
    ("    def _tui_hold_step(self, hold):\n", RUNTIME_METHODS + "    def _tui_hold_step(self, hold):\n"),
    ("                from hermes_cli.gateway_queue import OwnQueueHold\n"
     "                held = OwnQueueHold.begin(self, user_input)\n",
     "                if user_input is _RADIO_CONTEXT_RESET:\n"
     "                    self._radio_context_reset()\n"
     "                    continue\n"
     "                from hermes_cli.gateway_queue import OwnQueueHold\n"
     "                held = OwnQueueHold.begin(self, user_input)\n"),
    # apply_radio_gate.py and apply_radio_announce.py anchor on these two lines.
    ("            if hasattr(_signal, 'SIGHUP'):\n"
     "                _signal.signal(_signal.SIGHUP, self._tui_signal_handler)\n",
     "            if hasattr(_signal, 'SIGHUP'):\n"
     "                _signal.signal(_signal.SIGHUP, self._tui_signal_handler)\n"
     "            if hasattr(_signal, 'SIGUSR1'):\n"
     "                _signal.signal(_signal.SIGUSR1, self._tui_queue_radio_context_reset)\n"),
]


def patch(path: Path, edits, modules) -> None:
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        print(f"already patched: {path}")
        return
    for module in modules:
        if f"\nimport {module}\n" not in text:
            sys.exit(f"{path} no longer imports {module}; update this patch")
    for anchor, replacement in edits:
        if text.count(anchor) != 1:
            sys.exit(f"anchor not found exactly once in {path}:\n{anchor}")
        text = text.replace(anchor, replacement)
    compile(text, str(path), "exec")
    backup = path.with_name(path.name + ".pre-radio-base")
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
    patch(root / "hermes_cli" / "cli_voice_mixin.py", VOICE_EDITS, ("json", "os", "time"))
    patch(root / "hermes_cli" / "cli_tui_runtime_mixin.py", RUNTIME_EDITS, ("os", "queue"))


if __name__ == "__main__":
    main()
