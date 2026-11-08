#!/usr/bin/env python3
"""Install a 20k prompt-token rollover at a safe REPL boundary. Reapply after Hermes updates.

Requires radio_bridge installed by radio_bridge/install.py, and apply_radio_base.py + apply_radio_announce.py applied first. No extra model calls; no mid-task interruption.
"""
import py_compile
import shutil
import sys
from pathlib import Path

MARK = '# baofeng-radio-context v1'
METHODS = '''    # baofeng-radio-context v1
    def _radio_maybe_rollover(self):
        import sys as _sys
        from pathlib import Path as _Path
        _root = str(_Path("~/.local/share/baofeng-radio").expanduser())
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from radio_bridge.radio_context import maybe_rollover
        result = maybe_rollover(self)
        if result:
            from hermes_cli.cli_voice_mixin import _radio_dashboard_event
            _radio_dashboard_event("context_reset", f"Context renewed: {result['tokens']} tokens (limit {result['limit']}); last exchange kept.")

'''


def patch(root):
    path = Path(root) / 'hermes_cli/cli_tui_runtime_mixin.py'
    text = path.read_text()
    if MARK in text:
        return False
    edits = [
        ('    def _tui_idle_tick(self):\n', METHODS + '    def _tui_idle_tick(self):\n'),
        ('        self._check_config_mcp_changes()  # auto-reload MCP on mcp_servers change\n',
         '        self._check_config_mcp_changes()  # auto-reload MCP on mcp_servers change\n'
         '        if (os.environ.get("HERMES_RADIO_GATED") and\n'
         '                self._radio_announce_idle(os.path.expanduser(os.environ.get(\n'
         '                    "HERMES_RADIO_GATE_FILE", "~/.local/state/baofeng-radio/rx_gate")))):\n'
         '            self._radio_maybe_rollover()\n'),
        ('        self._agent_running = self._interactive_turn = True\n',
         '        self._radio_maybe_rollover()\n        self._agent_running = self._interactive_turn = True\n'),
    ]
    for anchor, replacement in edits:
        if text.count(anchor) != 1:
            raise RuntimeError(f'Expected exactly one anchor in {path}: {anchor!r}')
        text = text.replace(anchor, replacement)
    compile(text, str(path), 'exec')
    backup = path.with_name(path.name + '.pre-radio-context')
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text)
    py_compile.compile(str(path), doraise=True)
    return True


if __name__ == '__main__':
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '~/.hermes/hermes-agent').expanduser()
    print('patched' if patch(root) else 'already patched')
