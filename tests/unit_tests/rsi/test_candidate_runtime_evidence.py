# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime facts must survive the handoff without inventing task obligations."""

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.case_runner import _failure_signatures
from openjiuwen.rsi.harness_rsi.evaluator.judger import JudgeResult
from openjiuwen.rsi.harness_rsi.single_harness.iterative import (
    _candidate_can_continue_locally,
    _merge_repair_capabilities,
    _prior_candidate_feedback,
    _refresh_optimization_experience,
    _resume_fingerprint_matches,
)


@pytest.mark.parametrize("method", ["llm_as_judge", "exact_match", "script_based"])
@pytest.mark.parametrize("events", [[], [{"event_type": "workspace_change"}]])
def test_generic_failure_does_not_imply_a_patch_defect(method, events):
    assert _failure_signatures(events, JudgeResult(method, 0.0, False)) == []


@pytest.mark.parametrize("changed,expected", [(False, "workspace_edit_gap"), (True, "patch_quality_gap")])
def test_patch_evaluator_retains_patch_observations(changed, expected):
    events = [{"event_type": "workspace_change"}] if changed else []
    assert _failure_signatures(events, JudgeResult("swebench_official", 0.0, False)) == [expected]


def test_tool_errors_remain_visible_for_non_coding_tasks():
    events = [{"event_type": "tool_call", "data": {"exit_code": 1}}]
    assert _failure_signatures(events, JudgeResult("llm_as_judge", 0.0, False)) == ["tool_execution_failure"]
    assert _failure_signatures(events, JudgeResult("llm_as_judge", 1.0, True)) == []


@pytest.mark.parametrize("surface,invoked", [("prompt", []), ("skill", ["review"]), ("skill", [])])
def test_candidate_runtime_evidence_survives_journal_and_case_filter(tmp_path, surface, invoked):
    behavior = {
        "capabilities": [{"action_group": surface, "runtime_name": "review", "target_path": "review.md"}],
        "invoked_skill_names": invoked,
        "missing_skill_invocations": [],
    }
    state = {
        "candidate_gates": [{
            "status": "rejected", "capabilities": [{"action_group": surface}],
            "verifier_deltas_by_case": {"review_case": {}, "other_case": {}},
            "candidate_behavior_by_case": {"review_case": behavior, "other_case": {"private": True}},
        }],
    }
    _refresh_optimization_experience(state, tmp_path)
    feedback = _prior_candidate_feedback(state, [{"case_id": "review_case"}])
    assert set(feedback["by_case"]) == {"review_case"}
    assert feedback["by_case"]["review_case"][0]["candidate_behavior"] == behavior


@pytest.mark.parametrize("blocker", [
    None, "regressed_atomic_checks", "regressed_requirements", "regressed_fail_to_pass", "regressed_pass_to_pass",
    "missing_expected_skill_invocations", "missing_expected_tool_invocations", "failed_machine_evidence",
    "regressed_target_case_ids", "regressed_non_target_case_ids", "missing_evaluation", "different_cases",
])
def test_local_continuation_requires_scoped_nonregressing_evidence(tmp_path, blocker):
    ref = tmp_path / "eval.yaml"
    result = tmp_path / "result.json"
    result.write_text("{}", encoding="utf-8")
    ref.write_text(f'cases:\n- case_id: one\n  status: failed\n  score: 0\n  result_path: {result.as_posix()}\n',
                   encoding="utf-8")
    gate = {
        "status": "rejected", "reason": "candidate_made_partial_verifier_progress",
        "target_case_ids": ["one"], "candidate_eval_ref_path": str(ref),
        "verifier_deltas_by_case": {"one": {"partial_progress": True}},
    }
    if blocker in {"regressed_atomic_checks", "regressed_requirements", "regressed_fail_to_pass", "regressed_pass_to_pass"}:
        gate["verifier_deltas_by_case"]["one"][blocker] = ["previous_pass"]
    elif blocker == "missing_evaluation":
        gate["candidate_eval_ref_path"] = ""
    elif blocker == "different_cases":
        gate["target_case_ids"] = ["other"]
    elif blocker:
        gate[blocker] = ["blocked"]
    assert _candidate_can_continue_locally(gate, [{"case_id": "one"}]) is (blocker is None)
    assert gate["status"] == "rejected"


def test_repair_merge_preserves_prior_add_for_checkpoint_rollback():
    previous = [{"role": "solver", "target_path": "a", "operation": "add"},
                {"role": "solver", "target_path": "b", "operation": "add"}]
    latest = [{"role": "solver", "target_path": "a", "operation": "modify", "action_id": "latest"}]
    merged = _merge_repair_capabilities(previous, latest)
    assert len(merged) == 2
    assert merged[0]["operation"] == "add"
    assert merged[0]["action_id"] == "latest"
    assert latest[0]["operation"] == "modify"


def test_new_repair_chain_does_not_resume_old_independent_attempts():
    assert not _resume_fingerprint_matches(
        {"optimization_chain_version": 19}, {"optimization_chain_version": 20},
    )
