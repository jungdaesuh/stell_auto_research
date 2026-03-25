#!/usr/bin/env python3
"""Launch a Python entrypoint against a live simsopt tree using an existing env.

Run this script with ``python -S`` from a comparison environment to bypass any
editable-install redirectors in site-packages, then explicitly add:

1. the live repo's ``src`` directory
2. the environment's ``site-packages`` directory

This keeps the compiled extensions and third-party dependencies from the env
while forcing Python imports to resolve from the requested live source tree.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys


def _validate_dir(path: str, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise SystemExit(f"{label} is not a directory: {resolved}")
    return resolved


def _validate_file(path: str, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"{label} is not a file: {resolved}")
    return resolved


def _prepend_unique(path: Path) -> None:
    value = str(path)
    sys.path = [entry for entry in sys.path if entry != value]
    sys.path.insert(0, value)


def _append_unique(path: Path) -> None:
    value = str(path)
    if value not in sys.path:
        sys.path.append(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an entrypoint against a live simsopt source tree."
    )
    parser.add_argument(
        "--live-repo",
        required=True,
        help="Absolute path to the live simsopt repository root.",
    )
    parser.add_argument(
        "--site-packages",
        required=True,
        help="Absolute path to the Python env site-packages directory.",
    )
    parser.add_argument(
        "--print-resolution",
        action="store_true",
        help="Print the resolved simsopt import locations before running.",
    )
    parser.add_argument(
        "--chdir-script",
        action="store_true",
        help="Change into the target script directory before executing it.",
    )
    parser.add_argument(
        "script",
        help="Absolute path to the Python script to execute.",
    )
    parser.add_argument(
        "script_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to the target script.",
    )
    return parser.parse_args()


def print_resolution(live_src: Path, site_packages: Path) -> None:
    import simsopt
    import simsopt.geo.boozersurface as boozersurface

    print(f"cwd={Path.cwd()}")
    print(f"live_src={live_src}")
    print(f"site_packages={site_packages}")
    print(f"simsopt={Path(simsopt.__file__).resolve()}")
    print(f"boozersurface={Path(boozersurface.__file__).resolve()}")


def _change_to_script_dir(script: Path) -> None:
    os.chdir(script.parent)


def main() -> None:
    args = parse_args()
    live_repo = _validate_dir(args.live_repo, "--live-repo")
    site_packages = _validate_dir(args.site_packages, "--site-packages")
    script = _validate_file(args.script, "script")

    live_src = live_repo / "src"
    if not live_src.is_dir():
        raise SystemExit(f"Live repo src directory not found: {live_src}")

    _prepend_unique(live_src)
    _append_unique(site_packages)

    if args.print_resolution:
        print_resolution(live_src, site_packages)

    sys.argv = [str(script), *args.script_args]
    if args.chdir_script:
        _change_to_script_dir(script)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
