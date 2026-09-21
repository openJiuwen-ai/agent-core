"""VariantResult.error_tree is filled by the execution runner from stderr."""

from __future__ import annotations

import sys
from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    ImplementedVariant,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.agent import (
    ExperimentExecutionAgent,
)

_TRACE_SCRIPT = (
    "import argparse\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--method')\n"
    "p.add_argument('--output')\n"
    "p.parse_args()\n"
    "raise ValueError('paired_discordance')\n"
)

_OK_SCRIPT = (
    "import argparse, json\n"
    "from pathlib import Path\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--method')\n"
    "p.add_argument('--output')\n"
    "args = p.parse_args()\n"
    "Path(args.output).write_text(json.dumps({\n"
    "    'method': args.method, 'n_questions': 1,\n"
    "    'model_call_count': 1, 'per_question': [{'id': 'one', 'correct': True}],\n"
    "}))\n"
)

_CONTRACT_FAIL_SCRIPT = (
    "import argparse, json\n"
    "from pathlib import Path\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--method')\n"
    "p.add_argument('--output')\n"
    "args = p.parse_args()\n"
    "Path(args.output).write_text(json.dumps({\n"
    "    'method': args.method, 'n_questions': 0,\n"
    "    'model_call_count': 0, 'per_question': [],\n"
    "}))\n"
)


def _run(tmp_path: Path, script: str):
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    (code_dir / "run.py").write_text(script, encoding="utf-8")
    logs = tmp_path / "logs"
    results = tmp_path / "results"
    logs.mkdir()
    results.mkdir()
    agent = ExperimentExecutionAgent({})
    variant = ImplementedVariant(
        name="proposed",
        invocation=[sys.executable, "run.py", "--method", "proposed"],
    )
    return agent._run_variant(
        variant,
        code_dir=code_dir,
        logs=logs,
        results=results,
        timeout=10,
        max_transient_retries=0,
    )


def test_failed_variant_stores_error_tree_from_stderr(tmp_path: Path):
    result, _ = _run(tmp_path, _TRACE_SCRIPT)
    assert result.process_status == "failed"
    assert result.error_tree.startswith("Traceback (most recent call last):")
    assert "ValueError: paired_discordance" in result.error_tree


def test_completed_variant_has_empty_error_tree(tmp_path: Path):
    result, _ = _run(tmp_path, _OK_SCRIPT)
    assert result.process_status == "completed"
    assert result.error_tree == ""


def test_contract_failure_without_traceback_has_empty_error_tree(tmp_path: Path):
    result, _ = _run(tmp_path, _CONTRACT_FAIL_SCRIPT)
    assert result.process_status == "failed"
    assert result.error_tree == ""
