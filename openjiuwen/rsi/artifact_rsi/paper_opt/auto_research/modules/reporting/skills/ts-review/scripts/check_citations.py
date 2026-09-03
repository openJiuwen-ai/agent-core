#!/usr/bin/env python
"""CLI for the ts-review skill: check every \\cite key across all sections
against refs.bib's real keys, run by the reporting agent via its own
shell tool. See docs/paper_writing_design.md §6 — a fabricated citation is
a deterministic content bug, not noise.

Usage: python check_citations.py <workspace>   (<workspace> is the paper
workspace's absolute path, i.e. {PAPER_WORKSPACE} — reads sections/*.tex
and known_citation_keys.json from it, written before the agent session
starts. Falls back to the current directory if omitted, for direct/manual
invocation only.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import lint


def main() -> None:
    # workspace as an explicit argument, not Path.cwd() — see compile.py's
    # comment on why: the shell's tracked cwd can drift from an earlier
    # `cd` and silently redirect every relative path for the rest of the
    # session. Falls back to Path.cwd() only for direct/manual invocation;
    # every skill instruction passes {PAPER_WORKSPACE} explicitly.
    workspace = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    keys_path = workspace / "known_citation_keys.json"
    if not keys_path.is_file():
        print(json.dumps({"error": f"known_citation_keys.json not found under {workspace}"}))
        raise SystemExit(1)

    known_keys = set(json.loads(keys_path.read_text(encoding="utf-8")))
    sections_dir = workspace / "sections"
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(sections_dir.glob("*.tex"))
    )
    hallucinated = lint.check_citations(combined, known_keys)
    print(json.dumps({"hallucinated_keys": hallucinated}))


if __name__ == "__main__":
    main()
