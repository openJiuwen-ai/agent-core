#!/usr/bin/env python
"""CLI for the ts-figure skill: validate a MethodFigureSpec JSON against
the pydantic schema and against method.tex's real subsection headings.
Run by the agent before rendering, and again by the host after the
session (agent.py's _verify_and_build_output) — same "never trust, always
re-check" rule every other skill script in this module already follows.

Usage: python check_method_figure_spec.py <workspace> <spec_relative_path>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import lint
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.schemas import MethodFigureSpec


def main() -> None:
    if len(sys.argv) != 3:
        print(json.dumps({"error": "usage: check_method_figure_spec.py <workspace> <spec_relative_path>"}))
        raise SystemExit(1)
    workspace = Path(sys.argv[1]).resolve()
    spec_path = workspace / sys.argv[2]

    if not spec_path.is_file():
        print(json.dumps({"error": f"spec not found: {spec_path}"}))
        raise SystemExit(1)
    method_path = workspace / "sections" / "method.tex"
    if not method_path.is_file():
        print(json.dumps({"error": f"method.tex not found under {workspace} — run ts-write first."}))
        raise SystemExit(1)

    try:
        spec = MethodFigureSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        print(json.dumps({"error": "spec failed schema validation", "details": str(exc)}))
        raise SystemExit(1)

    headings = lint.extract_subsection_headings(method_path.read_text(encoding="utf-8"))
    bad_labels = lint.check_method_figure_headings([n.label for n in spec.nodes], headings)

    print(
        json.dumps(
            {
                "headings": headings,
                "invalid_node_labels": bad_labels,
                "passed": not bad_labels,
            }
        )
    )
    if bad_labels:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
