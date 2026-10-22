# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Batch-local progress uses evidenced criteria, never only aggregate score."""

import json

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.single_harness.iterative import (
    _candidate_can_continue_locally,
    _verifier_deltas_by_case,
)


@pytest.mark.parametrize("second_score,can_continue", [(0.4, True), (0.3, False)])
def test_partial_criterion_progress_is_kept_only_without_criterion_regression(tmp_path, second_score, can_continue):
    def evaluation(name, scores):
        root = tmp_path / name
        root.mkdir()
        result = root / "result.json"
        result.write_text(json.dumps({"evaluation": {"metadata": {"requirement_results": {
            "schema_version": 1, "items": [
                {"requirement_id": key, "group": "requirement", "passed": False, "score": score,
                 "evidence": "report.md includes the independently checked result"}
                for key, score in zip(("calculation", "proof"), scores)
            ],
        }}}}), encoding="utf-8")
        ref = root / "eval_ref.yaml"
        ref.write_text(yaml.safe_dump({"cases": [{"case_id": "one", "status": "failed", "score": 0,
                                                 "result_path": str(result)}]}), encoding="utf-8")
        return ref

    source = evaluation("source", [0.1, 0.4])
    candidate = evaluation("candidate", [0.5, second_score])
    delta = _verifier_deltas_by_case(source, candidate, {"one"})
    assert delta["one"]["partial_progress"] is can_continue
    gate = {"status": "rejected", "reason": "candidate_made_partial_verifier_progress",
            "target_case_ids": ["one"], "candidate_eval_ref_path": str(candidate),
            "verifier_deltas_by_case": delta}
    assert _candidate_can_continue_locally(gate, [{"case_id": "one"}]) is can_continue
    assert gate["status"] == "rejected"
