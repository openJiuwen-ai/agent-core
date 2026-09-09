"""Configured judge pass decisions survive optimization and epoch retention."""

import yaml
import pytest

from openjiuwen.rsi.harness_rsi.single_harness.iterative import (
    _eval_case_scores,
    _nonpassing_case_ids,
    _passing_case_ids,
    _select_gate_from_epoch_checkpoint,
    _sync_retained_case_ids,
)


@pytest.mark.parametrize("score,passed", [(0.799, False), (0.8, True), (0.965, True), (1.0, True)])
def test_llm_threshold_reaches_epoch_selection(tmp_path, score, passed):
    path = tmp_path / "eval_ref.yaml"
    path.write_text(yaml.safe_dump({"cases": [{
        "case_id": "task", "score": score, "status": "passed" if passed else "failed",
        "metadata": {"evaluation_method": "llm_as_judge", "evaluation_passed": passed},
    }]}), encoding="utf-8")
    expected = {"task"} if passed else set()
    assert _passing_case_ids(path) == expected
    assert _nonpassing_case_ids(str(path)) == {"task"} - expected
    assert _eval_case_scores(path) == {"task": score}
    retained = {"unrelated", "task"}
    _sync_retained_case_ids(retained, {"task": score}, passing_case_ids=_passing_case_ids(path))
    assert retained == {"unrelated"} | expected
    selection = _select_gate_from_epoch_checkpoint(
        {"target_case_ids": ["task"], "capabilities": []},
        full_eval_ref=str(path), error_case_ids=set(), machine_evidence_case_ids=set(),
    )
    assert selection["retained"] is passed


@pytest.mark.parametrize("method,score,explicit,status,expected", [
    ("script-based", 0.965, True, "passed", False),
    ("script-based", 1.0, True, "passed", True),
    ("llm_as_judge", 0.965, False, "failed", False),
    ("llm_as_judge", 0.965, True, "error", False),
    ("llm_as_judge", 0.965, True, "skipped", False),
])
def test_no_threshold_inference_or_infrastructure_promotion(tmp_path, method, score, explicit, status, expected):
    path = tmp_path / "eval_ref.yaml"
    path.write_text(yaml.safe_dump({"cases": [{
        "case_id": "task", "score": score, "status": status,
        "metadata": {"evaluation_method": method, "evaluation_passed": explicit},
    }]}), encoding="utf-8")
    assert ("task" in _passing_case_ids(path)) is expected
