"""Run with: python -m simulator_helper --config config.json run."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import ConfigurationError


def main(argv=None):
    parser = argparse.ArgumentParser(description="BTW Windows simulator observer")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Observe SimU and deliver captured results")
    commands.add_parser("diagnose", help="Show live crops and OCR without submitting")
    calibration = commands.add_parser("calibrate", help="Select one screen region with the mouse")
    calibration.add_argument("region", choices=("identifier", "score", "state"))
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            from .runtime import run
            run(args.config.resolve())
        elif args.command == "diagnose":
            from .runtime import diagnose
            diagnose(args.config.resolve())
        else:
            from .calibration import calibrate
            calibrate(args.config.resolve(), args.region)
    except KeyboardInterrupt:
        print("Simulator helper stopped safely.")
    except (ConfigurationError, RuntimeError, OSError) as exc:
        print(f"Simulator helper stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
