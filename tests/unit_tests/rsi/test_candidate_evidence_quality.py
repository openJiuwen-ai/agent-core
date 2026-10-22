# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unavailable runtime timing must not become a failed activation claim."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.config import AutoCoordinatingHarnessConfig
from openjiuwen.rsi.harness_rsi.single_harness.iterative import SingleHarnessIterativeOptimizationOrchestrator


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["skill", "tool"])
@pytest.mark.parametrize("status", ["complete", "incomplete", None])
async def test_missing_timing_requests_evidence_instead_of_claiming_late_activation(tmp_path, group, status):
    def write_eval(directory, score, execution):
        directory.mkdir(parents=True, exist_ok=True)
        result = directory / "result.json"
        result.write_text(json.dumps({"metadata": {"execution": execution}}), encoding="utf-8")
        ref = directory / "eval_ref.yaml"
        ref.write_text(yaml.safe_dump({"cases": [{
            "case_id": "one", "status": "passed" if score else "failed", "score": score,
            "result_path": str(result),
        }]}), encoding="utf-8")
        return str(ref)

    source = write_eval(tmp_path / "source", 0.0, {})

    class Evaluator:
        async def evaluate_batch(self, **kwargs):
            return write_eval(Path(kwargs["output_dir"]), 1.0, {
                **({"execution_evidence": {"status": status, "timing_complete": False}} if status else {}),
                "skill_triggers": [{"selected_skill_name": "check", "delivered": True}],
            })

    controller = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(), evaluator=Evaluator(), analyzer=object(), member_optimizer=object(),
    )
    analysis = tmp_path / "analysis_ref.yaml"
    analysis.write_text("issues: []\n", encoding="utf-8")
    analysis_calls = []

    async def analyze(**kwargs):
        analysis_calls.append(kwargs)
        return str(analysis)

    controller._analyze = analyze
    gate = await controller._candidate_gate(
        cases=[{"case_id": "one", "input": "Review the output"}], source_eval_ref=source,
        before_harness_refs_path=str(tmp_path / "source_refs.yaml"),
        candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"), member_status="success",
        capabilities=[{"action_group": group, "operation": "add", "runtime_name": "check",
                       "activation_phase": "post_diagnosis", "target_case_ids": ["one"]}],
        output_dir=tmp_path / "candidate", dataset=SimpleNamespace(dataset_id="test"),
    )
    assert gate["status"] == "inconclusive"
    assert gate["reason"] == "candidate_capability_evidence_inconclusive"
    assert gate["failure_class"] == "infrastructure_or_evidence_failure"
    assert gate["inconclusive_capability_case_ids"] == ["one"]
    assert not analysis_calls


def test_epoch_replay_treats_missing_runtime_capture_as_unknown(tmp_path):
    from openjiuwen.rsi.harness_rsi.single_harness.iterative import _select_gate_from_epoch_checkpoint

    result = tmp_path / "result.json"
    result.write_text(json.dumps({"metadata": {"execution": {"execution_evidence": {
        "status": "incomplete", "timing_complete": False, "reasons": ["missing_role_archive"],
    }}}}), encoding="utf-8")
    ref = tmp_path / "eval_ref.yaml"
    ref.write_text(yaml.safe_dump({"cases": [{"case_id": "one", "status": "passed", "score": 1,
                                             "result_path": str(result)}]}), encoding="utf-8")
    selection = _select_gate_from_epoch_checkpoint(
        {"target_case_ids": ["one"], "capabilities": [{"action_group": "skill", "runtime_name": "check",
                                                       "target_case_ids": ["one"]}]},
        full_eval_ref=str(ref), error_case_ids=set(), machine_evidence_case_ids=set(),
    )
    assert selection["retained"] is False
    assert selection["failure_class"] == "infrastructure_or_evidence_failure"
    assert selection["inconclusive_target_case_ids"] == ["one"]
    assert selection["missing_runtime_invocations"] == []
