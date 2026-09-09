#!/usr/bin/env python3
"""Ratchet check for broad `except Exception` usage.

Counts occurrences of `except Exception` across the repo's Python files and
compares against the committed baseline (except-baseline.json at repo root).

- Count went UP   -> exit 1 (new broad catches are not allowed; catch specific
                    types or use a designated boundary).
- Count went DOWN -> exit 0, and prints a reminder to lower the baseline
                    (or pass --update-baseline to do it in place).
- Count unchanged -> exit 0.

Usage:
    python scripts/check_except_baseline.py
    python scripts/check_except_baseline.py --update-baseline
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "except-baseline.json"
PATTERN = re.compile(r"except\s+Exception\b")

logger = logging.getLogger("check_except_baseline")


class _MaxLevelFilter(logging.Filter):
    """Pass only records strictly below ``level`` (keeps stdout free of errors)."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.level


def _configure_logging() -> None:
    """Route INFO output to stdout and WARNING+ to stderr with no prefix.

    This mirrors the previous ``print``/``print(..., file=sys.stderr)`` split so
    results stay pipe-friendly on stdout while failures go to stderr.
    """
    if logger.handlers:  # idempotent; avoid duplicate handlers on re-entry
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(_MaxLevelFilter(logging.WARNING))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)

    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    logger.propagate = False

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".semgrep",
}


def count_broad_excepts() -> tuple[int, dict[str, int]]:
    total = 0
    per_file: dict[str, int] = {}
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        n = len(PATTERN.findall(text))
        if n:
            total += n
            per_file[str(path.relative_to(REPO_ROOT))] = n
    return total, per_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the current count to except-baseline.json",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        metavar="N",
        help="also print the N files with the most occurrences",
    )
    args = parser.parse_args()

    _configure_logging()

    total, per_file = count_broad_excepts()

    if args.top:
        for f, n in sorted(per_file.items(), key=lambda kv: -kv[1])[: args.top]:
            logger.info("%5d  %s", n, f)

    if args.update_baseline or not BASELINE_FILE.exists():
        BASELINE_FILE.write_text(
            json.dumps({"except_exception": total}, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("baseline written: except_exception = %d", total)
        return 0

    baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    allowed = baseline.get("except_exception", 0)

    logger.info("broad 'except Exception' count: %d (baseline: %d)", total, allowed)
    if total > allowed:
        logger.error(
            "FAIL: %d new broad except block(s). Catch specific "
            "exception types, or handle at a designated boundary "
            "(see .semgrep/no-silent-except.yml for the boundary list).",
            total - allowed,
        )
        return 1
    if total < allowed:
        logger.info(
            "Improved by %d — run with --update-baseline to "
            "ratchet the baseline down.",
            allowed - total,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
