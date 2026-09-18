#!/usr/bin/env python
"""CLI for the ts-review skill: run lint.py's deterministic checks on one
section file, run by the reporting agent via its own shell tool. Thin
argv/stdout glue around already-tested functions — see
docs/paper_writing_design.md.

Usage: python lint_check.py <section_id> <workspace>   (<workspace> is the
paper workspace's absolute path, i.e. {PAPER_WORKSPACE} — reads
sections/<section_id>.tex and results.json from it, both written before
the agent session starts. Falls back to the current directory if omitted,
for direct/manual invocation only.)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    ExperimentResult,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import lint
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.sections import (
    LINT_OVERRIDES_FILENAME,
    apply_word_band_overrides,
    section_by_id,
)


def _load_overrides(workspace: Path) -> dict | None:
    path = workspace / LINT_OVERRIDES_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if len(sys.argv) not in (2, 3):
        logging.info(json.dumps({"error": "usage: lint_check.py <section_id> [workspace]"}))
        raise SystemExit(1)

    section_id = sys.argv[1]
    # workspace as an explicit second argument, not Path.cwd() — see
    # compile.py's comment on why: the shell's tracked cwd can drift from
    # an earlier `cd` and silently redirect every relative path for the
    # rest of the session. Falls back to Path.cwd() only for direct/manual
    # invocation; every skill instruction passes {PAPER_WORKSPACE} explicitly.
    workspace = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else Path.cwd()

    try:
        spec = section_by_id(section_id)
    except KeyError as exc:
        logging.info(json.dumps({"error": f"unknown section id: {section_id!r}"}))
        raise SystemExit(1) from exc

    section_path = workspace / "sections" / f"{section_id}.tex"
    if not section_path.is_file():
        logging.info(json.dumps({"error": f"section file not found: {section_path}"}))
        raise SystemExit(1)

    results_path = workspace / "results.json"
    if not results_path.is_file():
        logging.info(json.dumps({"error": f"results.json not found under {workspace}"}))
        raise SystemExit(1)

    overrides = _load_overrides(workspace)
    spec = apply_word_band_overrides(spec, overrides)
    extra_known = None
    if overrides and overrides.get("extra_known"):
        extra_known = {
            float(value)
            for value in overrides["extra_known"]
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }

    result = ExperimentResult.model_validate_json(results_path.read_text(encoding="utf-8"))
    text = section_path.read_text(encoding="utf-8")
    violations = lint.lint_section(text, spec, result, extra_known=extra_known)
    logging.info(json.dumps({"violations": violations}))


if __name__ == "__main__":
    main()
