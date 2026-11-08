#!/usr/bin/env python3
"""Build and upload the BF-777S classic ESP32 or ESP32-C3-MINI-1 firmware.

The first run creates .build-venv and installs PlatformIO there.  It never
installs Python packages into the system-managed Python environment.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
VENV_DIR = PROJECT_DIR / ".build-venv"
PIO_CORE_DIR = PROJECT_DIR / ".platformio-core"
CLASSIC_PROJECT_DIR = PROJECT_DIR / "firmware" / "BaofengRx"
C3_PROJECT_DIR = PROJECT_DIR / "firmware" / "BaofengRxC3"
# Both firmwares include this file; it holds your Wi-Fi credentials and host IP.
SECRETS = PROJECT_DIR / "firmware" / "BaofengRx" / "secrets.h"


def venv_python():
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_platformio():
    return VENV_DIR / (
        "Scripts/platformio.exe" if os.name == "nt" else "bin/platformio"
    )


def run(command, environment=None):
    print("+", " ".join(str(part) for part in command))
    subprocess.run(command, cwd=PROJECT_DIR, env=environment, check=True)


def platformio_environment():
    environment = os.environ.copy()
    environment["PLATFORMIO_CORE_DIR"] = str(PIO_CORE_DIR)
    environment["PLATFORMIO_DISABLE_TELEMETRY"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


def ensure_platformio():
    pio = venv_platformio()
    if pio.exists():
        return pio

    print("Preparing an isolated build environment in {}".format(VENV_DIR))
    if not venv_python().exists():
        try:
            run([sys.executable, "-m", "venv", VENV_DIR])
        except subprocess.CalledProcessError as error:
            raise SystemExit(
                "Could not create a virtual environment. On Debian/Ubuntu run:\n"
                "  sudo apt install python3-venv\n"
                "Then run this command again."
            ) from error

    python = venv_python()
    run([python, "-m", "pip", "install", "--upgrade", "pip"], platformio_environment())
    run([python, "-m", "pip", "install", "platformio"], platformio_environment())
    if not pio.exists():
        raise SystemExit("PlatformIO installation did not create the expected executable.")
    return pio


def main():
    parser = argparse.ArgumentParser(
        description="Compile and upload the classic ESP32 or ESP32-C3-MINI-1 Baofeng firmware."
    )
    parser.add_argument(
        "--port",
        help="ESP32 serial port, for example /dev/ttyUSB0 or COM3; auto-detect if omitted",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="open the 115200-baud serial monitor after upload",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="compile without uploading",
    )
    parser.add_argument(
        "--target",
        choices=("classic", "c3"),
        default="classic",
        help="firmware target: classic ESP32 (default) or ESP32-C3-MINI-1",
    )
    args = parser.parse_args()

    if not SECRETS.exists():
        raise SystemExit(
            "Missing {0}. Copy secrets.example.h next to it, then fill in your Wi-Fi\n"
            "networks and the LAN address of the PC that receives audio:\n"
            "  cp firmware/BaofengRx/secrets.example.h firmware/BaofengRx/secrets.h".format(SECRETS)
        )

    pio = ensure_platformio()
    environment = platformio_environment()
    firmware_project = C3_PROJECT_DIR if args.target == "c3" else CLASSIC_PROJECT_DIR

    build_command = [pio, "run", "--project-dir", firmware_project]
    if not args.build_only:
        build_command.extend(["-t", "upload"])
        if args.port:
            build_command.extend(["--upload-port", args.port])
    run(build_command, environment)

    if args.monitor:
        if args.build_only:
            raise SystemExit("--monitor requires an upload; omit --build-only.")
        monitor_command = [pio, "device", "monitor", "--baud", "115200"]
        if args.port:
            monitor_command.extend(["--port", args.port])
        run(monitor_command, environment)


if __name__ == "__main__":
    main()
