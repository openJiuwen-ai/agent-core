#!/usr/bin/env python
"""CLI for the ts-figure skill: list method.tex's real \\subsection{}
headings, run by the reporting agent via its own shell tool before it
authors a MethodFigureSpec. Thin argv/stdout glue around
lint.extract_subsection_headings — see docs/paper_writing_design.md.

Usage: python extract_headings.py <workspace>   (<workspace> is the paper
workspace's absolute path, i.e. {PAPER_WORKSPACE} — reads
sections/method.tex from it. Falls back to the current directory if
omitted, for direct/manual invocation only.)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import lint


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    workspace = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    method_path = workspace / "sections" / "method.tex"
    if not method_path.is_file():
        logging.info(json.dumps({"error": f"method.tex not found under {workspace} — run ts-write first."}))
        raise SystemExit(1)
    text = method_path.read_text(encoding="utf-8")
    headings = lint.extract_subsection_headings(text)
    if not headings:
        logging.info(json.dumps({"error": "no \\subsection{} headings found in method.tex", "headings": []}))
        raise SystemExit(1)
    logging.info(json.dumps({"headings": headings}))


if __name__ == "__main__":
    main()
