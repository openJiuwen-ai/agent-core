#!/usr/bin/env python
"""CLI for the ts-figure skill: copy a verified candidate figure over the
final path. Kept as a tiny host script rather than a shell `cp`/`copy`
so the instruction is the same on every OS this pipeline might run on.

Usage: python promote_figure.py <workspace> <candidate_relative_path> <final_relative_path>
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        print(json.dumps({"error": "usage: promote_figure.py <workspace> <candidate_relative_path> <final_relative_path>"}))
        raise SystemExit(1)
    workspace = Path(sys.argv[1]).resolve()
    candidate = workspace / sys.argv[2]
    final = workspace / sys.argv[3]
    if not candidate.is_file():
        print(json.dumps({"error": f"candidate not found: {candidate}"}))
        raise SystemExit(1)
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate, final)
    print(json.dumps({"promoted": str(final)}))


if __name__ == "__main__":
    main()
