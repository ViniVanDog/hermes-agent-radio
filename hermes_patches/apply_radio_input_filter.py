#!/usr/bin/env python3
"""Reject radio announcements before queuing voice input to Hermes' LLM.

Copies the standalone classifier into hermes_cli.radio_input_filter. This
patch applies only when HERMES_RADIO_GATED is set. STT accounting is retained.
"""

from pathlib import Path
import shutil
import sys
import time

MARK = '# baofeng-radio-input-filter v1'
NORMAL_ANCHOR = '                _radio_dashboard_event("stt", transcript)\n'
NORMAL_GUARD = '''                # baofeng-radio-input-filter v1
                if os.environ.get("HERMES_RADIO_GATED"):
                    from hermes_cli.radio_input_filter import radio_prompt_reason
                    if radio_prompt_reason(transcript):
                        radio_prompt_ignored = True
                        _radio_dashboard_event("radio_prompt_ignored", transcript)
                        _cprint(f"{_DIM}radio: radio prompt ignored before the LLM: {transcript}{_RST}")
                        return
'''
BARGE_ANCHOR = '            if transcript:\n                from tools.voice_mode_transcript import is_voice_stop_phrase\n'
BARGE_GUARD = '''            if transcript:
                # baofeng-radio-input-filter v1
                if os.environ.get("HERMES_RADIO_GATED"):
                    _radio_dashboard_event("stt", transcript)
                    from hermes_cli.radio_input_filter import radio_prompt_reason
                    if radio_prompt_reason(transcript):
                        _radio_dashboard_event("radio_prompt_ignored", transcript)
                        _cprint(f"{_DIM}radio: radio prompt ignored before the LLM: {transcript}{_RST}")
                        return
                from tools.voice_mode_transcript import is_voice_stop_phrase
'''


def install_helper(root, helper_source=None):
    helper = Path(helper_source) if helper_source else Path(__file__).resolve().parents[1] / 'radio_bridge/radio_input.py'
    data = helper.read_text()
    target = Path(root) / 'hermes_cli/radio_input_filter.py'
    compile(data, str(target), 'exec')
    if target.exists() and target.read_text() == data:
        return False
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + '.before-' + str(time.time_ns())))
    target.write_text(data)
    return True


def patch(root, helper_source=None):
    path = Path(root) / 'hermes_cli/cli_voice_mixin.py'
    source = path.read_text()
    if MARK in source:
        if source.count(MARK) != 2:
            raise RuntimeError('Filter incomplete: expected both voice paths patched')
        return install_helper(root, helper_source)
    edits = (
        (NORMAL_ANCHOR, NORMAL_ANCHOR + NORMAL_GUARD),
        ('        transcription_failed = False\n',
         '        transcription_failed = False\n        radio_prompt_ignored = False\n'),
        ('            elif not _activity_hold:\n',
         '            elif not _activity_hold and not radio_prompt_ignored:\n'),
        (BARGE_ANCHOR, BARGE_GUARD),
    )
    for old, new in edits:
        if source.count(old) != 1:
            raise RuntimeError('Voice source changed; anchor missing/ambiguous: {}'.format(old.strip()))
        source = source.replace(old, new)
    compile(source, str(path), 'exec')
    install_helper(root, helper_source)
    shutil.copy2(path, path.with_name(path.name + '.pre-radio-input-filter-' + str(time.time_ns())))
    path.write_text(source)
    return True


if __name__ == '__main__':
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '~/.hermes/hermes-agent').expanduser()
    print('patched' if patch(root, sys.argv[2] if len(sys.argv) > 2 else None) else 'already patched')
