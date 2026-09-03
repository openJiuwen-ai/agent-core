#!/usr/bin/env python
"""CLI for the ts-figure skill: deterministically splice a
\\includegraphics figure block into sections/method.tex, right after the
Problem Formulation subsection. Placement is host-decided, not left to
agent judgment — a whole-pipeline overview figure doesn't obviously
belong to any one of Method's per-component subsections (unlike the
Results figure, which has exactly one Results subsection to live in).

Usage: python insert_method_figure.py <workspace> <relative_figure_path> <caption>
  <relative_figure_path> is relative to the workspace (e.g.
  figures/method_figure.pdf); <caption> is one plain-text sentence.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PROBLEM_FORMULATION_RE = re.compile(
    r"\\subsection\*?\{\s*Problem Formulation\s*\}", re.IGNORECASE
)
_SUBSECTION_RE = re.compile(r"\\subsection\*?\{")


def _escape_latex_minimal(text: str) -> str:
    # Caption text is host-composed from the spec's own claim/takeaway
    # fields, not raw model prose — a light escape is enough here, this
    # is not the general-purpose escape_latex used for arbitrary content.
    return text.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")


def find_insertion_point(text: str) -> int:
    """End of the Problem Formulation subsection's content (just before
    the next \\subsection, or end of string if it's the last one). Falls
    back to right after the first \\subsection{} found if Problem
    Formulation isn't there, or the very end of the section if there are
    no subsections at all — never fails to find *some* insertion point."""
    m = _PROBLEM_FORMULATION_RE.search(text)
    if m is None:
        m = _SUBSECTION_RE.search(text)
    if m is None:
        return len(text)
    next_sub = _SUBSECTION_RE.search(text, m.end())
    return next_sub.start() if next_sub else len(text)


def main() -> None:
    if len(sys.argv) != 4:
        print(json.dumps({"error": "usage: insert_method_figure.py <workspace> <relative_figure_path> <caption>"}))
        raise SystemExit(1)
    workspace = Path(sys.argv[1]).resolve()
    figure_rel_path = sys.argv[2].replace("\\", "/")
    caption = sys.argv[3].strip()

    method_path = workspace / "sections" / "method.tex"
    if not method_path.is_file():
        print(json.dumps({"error": f"method.tex not found under {workspace}"}))
        raise SystemExit(1)

    text = method_path.read_text(encoding="utf-8")
    if "method_figure" in text and r"\includegraphics" in text:
        print(json.dumps({"error": "method.tex already contains a method_figure includegraphics — not inserting twice"}))
        raise SystemExit(1)

    figure_block = (
        "\n\n\\begin{figure}[h]\\centering"
        f"\\includegraphics[width=0.95\\linewidth]{{{figure_rel_path}}}"
        f"\\caption{{{_escape_latex_minimal(caption)}}}"
        "\\label{fig:method}"
        "\\end{figure}\n\n"
    )

    idx = find_insertion_point(text)
    new_text = text[:idx].rstrip() + figure_block + text[idx:].lstrip("\n")
    method_path.write_text(new_text, encoding="utf-8")
    print(json.dumps({"inserted_at_char": idx, "figure_path": figure_rel_path}))


if __name__ == "__main__":
    main()
