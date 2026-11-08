#!/usr/bin/env python3
"""Apply the Baofeng radio-speech patch to a Hermes Agent checkout.

Idempotent: re-run it after `hermes update` overwrites the local changes.
Only active when Hermes runs with HERMES_RADIO_GATED=1 (hermes-radio.service),
and HERMES_RADIO_SPEECH=0 turns it off without unpatching.

It copies radio_speech.py to ~/.local/share/baofeng-radio/ and makes Hermes'
Edge TTS call it: real pauses between sentences (Edge itself ignores "..",
"..." and line breaks) and pt-BR fixes for times, "2x", units and symbols.
Any failure there falls back to the original single Edge request.

Usage: python3 apply_radio_speech.py [~/.hermes/hermes-agent]
"""

import py_compile
import shutil
import sys
from pathlib import Path

MARK = "# baofeng-radio-speech"
MODULE_DEST = Path("~/.local/share/baofeng-radio/radio_speech.py").expanduser()

ANCHOR = '''    await edge_tts.Communicate(text, **kwargs).save(output_path)
    return output_path
'''
PATCH = '''    # baofeng-radio-speech: fixed pauses + spoken-text fixes for the radio (hermes_patches/radio_speech.py)
    if os.environ.get("HERMES_RADIO_GATED") == "1" and os.environ.get("HERMES_RADIO_SPEECH", "1") != "0":
        try:
            import importlib.util as _radio_ilu
            _radio_spec = _radio_ilu.spec_from_file_location("baofeng_radio_speech", os.path.expanduser(
                os.environ.get("HERMES_RADIO_SPEECH_MODULE", "~/.local/share/baofeng-radio/radio_speech.py")))
            _radio = _radio_ilu.module_from_spec(_radio_spec)
            _radio_spec.loader.exec_module(_radio)
            return await _radio.synthesize_edge(edge_tts, text, output_path, kwargs["voice"], kwargs.get("rate"))
        except Exception as _radio_exc:
            logging.getLogger(__name__).warning("baofeng radio speech failed, plain Edge TTS: %s", _radio_exc)
''' + ANCHOR


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/hermes-agent").expanduser()
    MODULE_DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve().parent / "radio_speech.py", MODULE_DEST)
    print(f"module: {MODULE_DEST}")

    providers = root / "tools" / "tts_tool_providers.py"
    text = providers.read_text(encoding="utf-8")
    if MARK in text:
        print(f"already patched: {providers}")
        return
    start = text.find("async def _generate_edge_tts(")
    end = text.find("\n\n\n", start) + 1   # keep the body's final newline
    body = text[start:end]
    if start < 0 or body.count(ANCHOR) != 1:
        sys.exit(f"_generate_edge_tts changed in {providers}; update this patch")
    for module in ("os", "logging"):
        if f"\nimport {module}\n" not in text:
            sys.exit(f"{providers} no longer imports {module}; update this patch")
    patched = text[:start] + body.replace(ANCHOR, PATCH) + text[end:]
    backup = providers.with_name(providers.name + ".pre-radio-speech")
    if not backup.exists():
        shutil.copy2(providers, backup)
    providers.write_text(patched, encoding="utf-8")
    try:
        py_compile.compile(str(providers), doraise=True)
    except py_compile.PyCompileError as error:
        shutil.copy2(backup, providers)
        sys.exit(f"patched file did not compile, restored backup: {error}")
    print(f"patched: {providers}")


if __name__ == "__main__":
    main()
