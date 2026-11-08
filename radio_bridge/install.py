#!/usr/bin/env python3
"""Copy radio_bridge to ~/.local/share/baofeng-radio so the Hermes patches can import it.

Run it on the Hermes VM, as the user that runs Hermes, before
apply_radio_context.py, and again after you change anything in radio_bridge/.

    python3 radio_bridge/install.py [--dest ~/.local/share/baofeng-radio]
"""

import argparse
from pathlib import Path
import shutil

SOURCE = Path(__file__).resolve().parent


def install(dest):
    target = Path(dest).expanduser() / "radio_bridge"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in sorted(SOURCE.glob("*.py")):
        if path.name.startswith("test_") or path.name == "install.py":
            continue
        shutil.copy2(path, target / path.name)
        copied.append(path.name)
    return target, copied


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", default="~/.local/share/baofeng-radio")
    args = parser.parse_args()
    target, copied = install(args.dest)
    print(f"installed {', '.join(copied)} -> {target}")


if __name__ == "__main__":
    main()
