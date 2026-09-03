#!/usr/bin/env python
"""CLI for the ts-figure skill: verify an agent-authored results-figure
script before trusting it. Run twice by design — once by the agent itself
during the session (to get feedback it can act on), and once more,
independently, by the host after the session ends (agent.py's
_verify_and_build_output never trusts the agent's own report of "done",
same rule code_implementation's smoke-test re-run already applies).

Checks, in order:
1. static fabrication scan of the script's own source
   (lint.scan_script_for_fabrication) against real result.variants values
2. a fresh subprocess re-run of the script (never trust a prior run's
   side effects) with a bounded timeout
3. the expected output file actually exists and is non-empty afterward

Usage: python check_figure_script.py <workspace> <script_relative_path> <expected_output_relative_path>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentResult
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import lint

_TIMEOUT_SECONDS = 60


def main() -> None:
    if len(sys.argv) != 4:
        print(json.dumps({"error": "usage: check_figure_script.py <workspace> <script_relative_path> <expected_output_relative_path>"}))
        raise SystemExit(1)
    workspace = Path(sys.argv[1]).resolve()
    script_rel = sys.argv[2]
    output_rel = sys.argv[3]

    script_path = workspace / script_rel
    if not script_path.is_file():
        print(json.dumps({"error": f"script not found: {script_path}"}))
        raise SystemExit(1)

    results_path = workspace / "results.json"
    if not results_path.is_file():
        print(json.dumps({"error": f"results.json not found under {workspace}"}))
        raise SystemExit(1)

    result = ExperimentResult.model_validate_json(results_path.read_text(encoding="utf-8"))
    known = lint.known_numbers(result)
    source = script_path.read_text(encoding="utf-8")
    fabricated = sorted(set(lint.scan_script_for_fabrication(source, known)))

    output_path = workspace / output_rel
    if output_path.exists():
        output_path.unlink()  # never trust a stale file from a prior run

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
        ran_ok = proc.returncode == 0
        run_error = None if ran_ok else (proc.stderr or proc.stdout)[-2000:]
    except subprocess.TimeoutExpired:
        ran_ok = False
        run_error = f"script timed out after {_TIMEOUT_SECONDS}s"

    output_exists = output_path.is_file() and output_path.stat().st_size > 0

    print(
        json.dumps(
            {
                "fabricated_numbers": fabricated,
                "ran_ok": ran_ok,
                "run_error": run_error if not ran_ok else None,
                "output_exists": output_exists,
                "passed": ran_ok and output_exists and not fabricated,
            }
        )
    )
    if not (ran_ok and output_exists and not fabricated):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
