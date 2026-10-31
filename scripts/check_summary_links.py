#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Fail if docs/*/SUMMARY.md TOC links point at missing files."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    bad: list[str] = []
    for lang in ("zh", "en"):
        path = ROOT / "docs" / lang / "SUMMARY.md"
        if not path.is_file():
            bad.append(f"missing {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\]\(([^)]+\.md)\)", text):
            target = unquote(match.group(1).split("#", 1)[0])
            line = text[: match.start()].count("\n") + 1
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                bad.append(f"{path.relative_to(ROOT)}:{line}  {target}")
    for item in bad:
        print(f"BROKEN {item}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
