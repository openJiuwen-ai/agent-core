#!/usr/bin/env python
"""CLI for the ts-latex skill: assemble main.tex from title.txt +
sections/*.tex and compile it, run by the reporting agent via its own
shell tool. Thin argv/stdout glue around latex.py's already-tested
functions — see docs/paper_writing_design.md.

Usage: python compile.py <workspace>   (<workspace> is the paper workspace's
absolute path, i.e. {PAPER_WORKSPACE} — reads title.txt and sections/*.tex
from it, writes main.tex/main.pdf there. Falls back to the current
directory if omitted, for direct/manual invocation only.)

If latexmk/pdflatex aren't already on PATH for whatever process runs the
pipeline, set LATEX_BIN_DIR (in .env, or reporting.latex_bin_dir in
configs/pipeline.default.yaml — agent.py copies the config value into this
env var before invoking the reporting agent) to your TeX distribution's bin
directory. Do not hardcode a machine-specific path here directly — a live
run once did exactly that (the agent has write access to this file, since
skill discovery needs project_root in its sandbox) after latexmk/pdflatex
were missing from PATH, baking one developer's local install path into
tracked source. That's the actual bug this env var exists to make
impossible to repeat: there is no path here for it to "helpfully" hardcode.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.latex import (
    assemble_document,
    compile_document,
    extract_compile_errors,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.reflow import reflow
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.sections import DOCUMENT_ORDER

# Vendored verbatim from spark-to-paper-skills' ts-paper/templates/
# neurips_official/ (the real, official NeurIPS 2025 style, "official":
# true in its own template.json) — see latex.py's _PREAMBLE docstring.
_NEURIPS_STY = Path(__file__).parent.parent / "assets" / "neurips_2025.sty"


def main() -> None:
    latex_bin_dir = os.environ.get("LATEX_BIN_DIR", "").strip()
    if latex_bin_dir and os.path.isdir(latex_bin_dir):
        os.environ["PATH"] = latex_bin_dir + os.pathsep + os.environ.get("PATH", "")
    # Takes the workspace as an explicit argument rather than trusting
    # Path.cwd() — the agent's shell tool tracks one mutable cwd shared
    # across every tool call in the session, and a single stray `cd`
    # anywhere earlier in the session (e.g. while running a different
    # skill's script) silently redirects every relative path for the rest
    # of it, including this one. An explicit absolute argument is immune
    # to that regardless of what the shell's tracked cwd has drifted to.
    # Falls back to Path.cwd() only for direct/manual invocation (e.g. a
    # human running this script by hand) — every skill instruction passes
    # {PAPER_WORKSPACE} explicitly.
    workspace = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    title_path = workspace / "title.txt"
    if not title_path.is_file():
        print(json.dumps({"success": False, "error": "title.txt not found — run ts-plan first."}))
        raise SystemExit(1)
    title = title_path.read_text(encoding="utf-8").strip()

    keywords_path = workspace / "keywords.txt"
    keywords = keywords_path.read_text(encoding="utf-8").strip() if keywords_path.is_file() else None

    sty_dst = workspace / _NEURIPS_STY.name
    if _NEURIPS_STY.is_file() and not sty_dst.is_file():
        shutil.copy2(_NEURIPS_STY, sty_dst)

    sections_dir = workspace / "sections"
    section_bodies: dict[str, str] = {}
    for section_id in DOCUMENT_ORDER:
        section_path = sections_dir / f"{section_id}.tex"
        if section_path.is_file():
            # Normalize to one logical line per paragraph before assembly —
            # PDF-neutral (a single '\n' is just a space in LaTeX), makes
            # the .tex source readable, and is idempotent, so re-running
            # compile after a repair edit never re-wraps what's already clean.
            raw = section_path.read_text(encoding="utf-8")
            normalized = reflow(raw)
            if normalized != raw:
                section_path.write_text(normalized, encoding="utf-8")
            section_bodies[section_id] = normalized

    tex_path = workspace / "main.tex"
    tex_path.write_text(
        assemble_document(
            title=title, section_bodies=section_bodies, document_order=DOCUMENT_ORDER, keywords=keywords
        ),
        encoding="utf-8",
    )

    result = compile_document(tex_path)
    print(
        json.dumps(
            {
                "success": result.success,
                "pdf_path": str(tex_path.with_suffix(".pdf")) if result.success else None,
                "error_lines": extract_compile_errors(result.log_tail),
                "log_tail": result.log_tail[-2000:],
            }
        )
    )


if __name__ == "__main__":
    main()
