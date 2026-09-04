"""Run one official Evo-Bench validation task through WSL local isolation."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.rsi.evobench.run_one import main

if __name__ == "__main__":
    raise SystemExit(main())
