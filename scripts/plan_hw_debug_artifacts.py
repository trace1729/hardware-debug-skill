#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from hw_debug_cli import main as hw_debug_main


def main() -> int:
    return hw_debug_main(["inspect-inputs", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
