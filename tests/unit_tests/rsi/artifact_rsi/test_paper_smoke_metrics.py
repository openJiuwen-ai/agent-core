"""Regression coverage for codegen metrics rejected despite item_records."""

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    validate_metrics_contract,
    validate_smoke_live_path,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.agent import (
    CandidateValidation,
    CodeImplementationAgent,
)


@pytest.mark.parametrize("key", ["per_question", "task_records", "records", "item_records"])
def test_smoke_accepts_item_record_aliases(key):
    payload = {
        "method": "react_baseline", "n_questions": 1, "model_call_count": 1,
        key: [{"id": "case-01", "correct": True}],
    }
    assert validate_metrics_contract(payload, expected_method="react_baseline").ok
    assert validate_smoke_live_path(payload).ok


@pytest.mark.parametrize("records", [[], ["not an object"]])
def test_alias_does_not_allow_empty_or_invalid_records(records):
    payload = {"n_questions": 1, "model_call_count": 1, "item_records": records}
    assert not validate_metrics_contract(payload).ok
    result = validate_smoke_live_path(payload)
    assert not result.ok
    assert "per_question" in result.errors[0]


def test_alias_keeps_count_failure_and_canonical_precedence_checks():
    payload = {"n_questions": 2, "model_call_count": 1, "item_records": [{"id": "one"}]}
    assert not validate_metrics_contract(payload).ok
    payload.update(n_questions=1, per_question=[])
    assert not validate_smoke_live_path(payload).ok
    del payload["per_question"]
    payload["model_call_count"] = 0
    assert not validate_smoke_live_path(payload).ok
    payload.update(status="completed", item_records=[{"status": "failed"}])
    assert not validate_metrics_contract(payload).ok


def test_repeated_metrics_failure_guides_writer_repair():
    validation = CandidateValidation(ok=False, stage="metrics", repeated=True)
    prompt = CodeImplementationAgent._build_validation_repair_prompt(validation)
    assert "per_question=" in prompt
    assert "metrics writer" in prompt
    assert "Isolate the smallest failing SDK call" not in prompt


def test_repeated_non_metrics_failure_keeps_sdk_guidance():
    validation = CandidateValidation(ok=False, stage="smoke", repeated=True)
    prompt = CodeImplementationAgent._build_validation_repair_prompt(validation)
    assert "Isolate the smallest failing SDK call" in prompt


def test_smoke_subprocess_receives_supplied_paper(tmp_path):
    """Generated code reads the same input in smoke and full execution."""
    code_dir = tmp_path / "code"
    code_dir.mkdir()
    paper = tmp_path / "paper.tex"
    paper.write_text("supplied evidence", encoding="utf-8")
    (code_dir / "run.py").write_text(
        "import argparse, json, os\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--method'); p.add_argument('--smoke-test', action='store_true')\n"
        "p.add_argument('--output'); args = p.parse_args()\n"
        "assert Path(os.environ['ARTIFACT_PATH']).read_text() == 'supplied evidence'\n"
        "Path(args.output).write_text(json.dumps({'method': args.method, 'n_questions': 1, "
        "'model_call_count': 1, 'per_question': [{'id': 'one', 'correct': True}]}))\n",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    agent = CodeImplementationAgent({"code_implementation": {"smoke_test_timeout_seconds": 10}})
    result = agent._run_smoke_and_metrics(
        "input-check", code_dir, ["proposed"], cycle=1,
        log_dir=log_dir, candidate_hash="test", artifact_path=str(paper),
    )
    assert result.ok, result.errors
