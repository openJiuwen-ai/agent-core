"""Run one official Evo-Bench validation task through WSL local isolation."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.rsi.evobench.run_one import main


if __name__ == "__main__":
    raise SystemExit(main())
