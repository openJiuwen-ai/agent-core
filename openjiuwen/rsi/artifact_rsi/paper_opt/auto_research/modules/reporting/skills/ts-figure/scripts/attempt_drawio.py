#!/usr/bin/env python
"""CLI for the ts-figure skill: check whether the Draw.io + paper-icons
path is actually usable on this machine before the agent tries it.

Deliberately does NOT vendor qhy991/Scientific-Figure-Design's
build_drawio_figure.py here: that script calls discover_paper_icons()
unconditionally at the top of main() (confirmed by reading its source),
so it cannot run at all without a discoverable ~580-icon catalog on
disk, even for a spec that uses zero icons. Vendoring the script without
its catalog would ship dead code; vendoring the catalog too is a much
bigger, separately-reviewable decision (license manifest, repo size) than
"add a figure renderer" — deferred, not done here. There is also no
``drawio`` CLI in this project's dev environment to ever exercise the
export step against, so this path is currently unexercised regardless.

This script reports what's actually available so ts-figure's SKILL.md can
give the agent a real decision instead of an aspirational one:
- DRAWIO_BIN (or `drawio`/`draw.io` on PATH): the export binary.
- DRAWIO_SKILL_DIR: an external checkout of the real skill (with its own
  build_drawio_figure.py + assets/paper-icons/), if you've installed one.

If both are present, ts-figure may shell out to
``$DRAWIO_SKILL_DIR/scripts/build_drawio_figure.py``. If either is
missing, it must use render_method_figure.py (the matplotlib path)
instead — never partially attempt Draw.io and fail silently.

Usage: python attempt_drawio.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path


def find_drawio_binary() -> str | None:
    candidates = [
        os.environ.get("DRAWIO_BIN"),
        shutil.which("drawio"),
        shutil.which("draw.io"),
        "/Applications/draw.io.app/Contents/MacOS/draw.io",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path.resolve())
    return None


def find_skill_dir() -> str | None:
    raw = os.environ.get("DRAWIO_SKILL_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    build_script = path / "scripts" / "build_drawio_figure.py"
    catalog = path / "assets" / "paper-icons" / "library" / "catalog.json"
    if build_script.is_file() and catalog.is_file():
        return str(path.resolve())
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    drawio_bin = find_drawio_binary()
    skill_dir = find_skill_dir()
    available = drawio_bin is not None and skill_dir is not None
    logging.info(
        json.dumps(
            {
                "available": available,
                "drawio_bin": drawio_bin,
                "skill_dir": skill_dir,
                "reason": None
                if available
                else "missing "
                + " and ".join(
                    name
                    for name, present in (("DRAWIO_BIN", drawio_bin), ("DRAWIO_SKILL_DIR", skill_dir))
                    if not present
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
