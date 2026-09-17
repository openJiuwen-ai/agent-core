# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Historical candidate wins must not erase a diagnosis of a later failure."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    DeterministicSignals,
)


def _diagnosis(check="delivery_check"):
    return {
        "issue_category": "member_harness",
        "severity": "high",
        "summary": "The latest execution stopped before producing the required artifact.",
        "failure_mode": "investigation_without_delivery",
        "failure_cluster": {
            "failed_checks": [check],
            "observable_behavior": "The latest execution produced no artifact.",
        },
        "root_cause": "The solver repeated an observation instead of acting on its conclusion.",
        "target_ref": "member_harness.solver.prompt",
        "evidence_refs": [],
        "confidence": "high",
    }


@pytest.mark.parametrize("delta_key", ["newly_passed_fail_to_pass", "newly_passed_atomic_checks"])
def test_historical_success_does_not_remove_a_current_diagnosis(delta_key):
    diagnosis = _diagnosis()
    feedback = {"experiments": [{"verifier_delta": {delta_key: ["delivery_check"]}}]}
    original = deepcopy(feedback)

    result = analyzer._normalize_case_diagnoses(
        {"diagnoses": [diagnosis]},
        prior_candidate_feedback=feedback,
    )

    assert result == [diagnosis]
    assert feedback == original


def test_historical_checks_prioritize_but_do_not_veto_diagnoses():
    feedback = {
        "experiments": [
            {
                "verifier_delta": {
                    "newly_passed_atomic_checks": ["previous_win"],
                    "remaining_failed_atomic_checks": ["remaining"],
                    "regressed_atomic_checks": ["regression"],
                }
            }
        ]
    }
    result = analyzer._normalize_case_diagnoses(
        {"diagnoses": [_diagnosis(name) for name in ("previous_win", "remaining", "regression")]},
        prior_candidate_feedback=feedback,
    )

    assert [row["failure_cluster"]["failed_checks"][0] for row in result] == [
        "regression",
        "remaining",
        "previous_win",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,metadata", [("swebench_official", {"empty_patch": True}), ("llm_as_judge", {})])
async def test_latest_failure_survives_pipeline_without_format_repair(monkeypatch, method, metadata):
    strategy = analyzer.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    monkeypatch.setattr(strategy, "_build_agent", AsyncMock(return_value=object()))
    monkeypatch.setattr(analyzer, "_prepare_diagnosis_evidence", lambda **kwargs: False)
    run = AsyncMock(return_value=json.dumps({"diagnoses": [_diagnosis()]}))
    monkeypatch.setattr(analyzer, "_run_agent", run)
    case = CaseAnalysisInput(
        case_id="current_failure",
        status="failed",
        score=0.0,
        input="Produce an artifact.",
        expected=None,
        response="",
        error="",
        evaluation_method=method,
        evaluation_passed=False,
        evaluation_reason="No artifact delivered.",
        evaluation_metadata=metadata,
        trace_path="",
        result_path="",
    )
    results = await strategy._per_case_diagnosis(
        [case],
        DeterministicSignals(method=method),
        None,
        prior_candidate_feedback={
            "by_case": {
                case.case_id: [
                    {
                        "verifier_delta": {"newly_passed_atomic_checks": ["delivery_check"]},
                    }
                ]
            }
        },
    )

    assert run.await_count == 1
    assert len(results) == 1
    assert not results[0].get("analysis_failed")
    assert results[0]["target_ref"] == "member_harness.solver.prompt"


@pytest.mark.parametrize("raw", ['{"diagnoses": []}', '{"diagnoses": "invalid"}'])
def test_valid_json_without_diagnoses_is_not_reported_as_missing_json(raw):
    error = analyzer._unusable_diagnosis_output_error("case", [raw])

    assert "did not contain JSON" not in str(error)
    assert "no usable diagnosis" in str(error)
    prompt = analyzer._build_json_repair_prompt("Original evidence", raw)
    assert "not valid JSON" not in prompt
    assert "no usable diagnosis" in prompt


def test_json_service_error_is_not_reclassified_as_diagnosis_content():
    error = analyzer._unusable_diagnosis_output_error("case", ['{"error": "invalid_api_key"}'])

    assert type(error) is ValueError
    assert "model-service error" in str(error)


@pytest.mark.asyncio
async def test_empty_diagnosis_content_stays_unavailable_without_format_misclassification(monkeypatch):
    strategy = analyzer.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    monkeypatch.setattr(strategy, "_build_agent", AsyncMock(return_value=object()))
    monkeypatch.setattr(analyzer, "_prepare_diagnosis_evidence", lambda **kwargs: False)
    run = AsyncMock(return_value='{"diagnoses": []}')
    monkeypatch.setattr(analyzer, "_run_agent", run)
    case = CaseAnalysisInput(
        case_id="case",
        status="failed",
        score=0.0,
        input="Produce an artifact.",
        expected=None,
        response="",
        error="",
        evaluation_method="unit_test",
        evaluation_passed=False,
        evaluation_reason="Missing artifact",
        evaluation_metadata={},
        trace_path="",
        result_path="",
    )

    results = await strategy._per_case_diagnosis([case], DeterministicSignals(), None)

    assert run.await_count == 2
    assert results[0]["analysis_failed"] is True
    assert results[0]["diagnosis_error_type"] == "diagnosis_content"
    assert "not valid JSON" not in run.await_args.args[1]
