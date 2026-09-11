# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A bounded summary must not make earlier execution evidence unrecoverable."""

import json

import pytest

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    DeterministicSignals,
)


@pytest.fixture
def history_case(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "_prepare_repository_snapshot", lambda **kwargs: False)
    case_dir = tmp_path / "case"
    (case_dir / "judge").mkdir(parents=True)
    trace = {
        "private_metadata": "not_execution_evidence",
        "traces": [
            {
                "trace_id": "trace-a",
                "member_role": "worker",
                "messages": [
                    {"role": "system", "content": "private_system_configuration"},
                    {
                        "role": "assistant",
                        "message_index": 1,
                        "step_pointer": "step_2",
                        "content": "The owner was inspected and a repair location was identified.",
                        "reasoning_content": "private_reasoning",
                    },
                    {
                        "role": "assistant",
                        "message_index": 2,
                        "tool_calls": [
                            {
                                "name": "read_file",
                                "input": {"path": "owner.py"},
                                "output": "prefix " * 100 + "EARLY_OBSERVATION" + " suffix" * 100,
                                "step_pointer": "step_3",
                                "private_metadata": "private_call_metadata",
                            }
                        ],
                    },
                    *[
                        {"role": "assistant", "message_index": i, "content": "Repeated late inspection"}
                        for i in range(3, 32)
                    ],
                ],
            }
        ],
    }
    (case_dir / "judge/normalized_trace.json").write_text(json.dumps(trace), encoding="utf-8")
    return CaseAnalysisInput(
        case_id="example",
        status="completed",
        score=0.0,
        input="Prepare the requested result.",
        expected=None,
        response="",
        error="",
        evaluation_method="custom",
        evaluation_passed=False,
        evaluation_reason="No deliverable",
        evaluation_metadata={},
        trace_path="",
        result_path=str(case_dir / "result.json"),
    )


def test_early_evidence_is_recoverable_beyond_summary_tail(history_case, tmp_path):
    runtime = tmp_path / "runtime"
    assert analyzer._prepare_diagnosis_evidence(case=history_case, runtime_dir=runtime)
    summary = (runtime / "evidence_summary.md").read_text(encoding="utf-8")
    assert "EARLY_OBSERVATION" not in summary
    history = json.loads((runtime / "execution_history.json").read_text(encoding="utf-8"))
    message = history["traces"][0]["messages"][1]
    assert message["message_index"] == 2
    assert "EARLY_OBSERVATION" in message["tool_calls"][0]["output"]
    assert message["tool_calls"][0]["step_pointer"] == "step_3"


def test_history_exports_only_observed_message_and_tool_fields(history_case, tmp_path):
    runtime = tmp_path / "runtime"
    analyzer._prepare_diagnosis_evidence(case=history_case, runtime_dir=runtime)
    text = (runtime / "execution_history.json").read_text(encoding="utf-8")
    assert "The owner was inspected" in text
    for excluded in (
        "not_execution_evidence",
        "private_system_configuration",
        "private_reasoning",
        "private_call_metadata",
    ):
        assert excluded not in text


def test_history_pointer_is_not_lost_to_inline_summary_limit(history_case, tmp_path):
    runtime = tmp_path / "runtime"
    analyzer._prepare_diagnosis_evidence(case=history_case, runtime_dir=runtime)
    prompt = analyzer._build_diagnosis_prompt(
        case=history_case,
        signals=DeterministicSignals(),
        retrieved_experience={},
        evidence_summary_available=True,
    )
    assert "execution_history.json" in prompt
    assert "excerpts, not the complete execution history" in prompt
    assert str(tmp_path) not in prompt


@pytest.mark.parametrize("value", [{}, {"traces": None}, {"traces": [None, {"messages": None}]}])
def test_missing_or_malformed_history_is_not_fabricated(history_case, tmp_path, value):
    path = tmp_path / "case/judge/normalized_trace.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    runtime = tmp_path / "runtime"
    analyzer._prepare_diagnosis_evidence(case=history_case, runtime_dir=runtime)
    history = json.loads((runtime / "execution_history.json").read_text(encoding="utf-8"))
    assert history == {"traces": []}
