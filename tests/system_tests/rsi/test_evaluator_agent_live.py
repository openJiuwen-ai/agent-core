# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Opt-in real-model checks for the generic evaluator agent, without a task-agent run."""

import json
import os

import pytest

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger

MODEL_CONFIG = os.getenv("RSI_JUDGE_LIVE_MODEL_CONFIG", "")
pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MODEL_CONFIG, reason="requires an explicit live model config"),
]


def _judge():
    return LlmAsJudgeJudger(
        EvaluatorConfig(
            evaluation_method="llm_as_judge",
            judge_model_config_ref=MODEL_CONFIG,
            judge_agent_max_iterations=6,
            judge_agent_max_tokens=4096,
            judge_timeout_sec=180,
            judge_max_retries=0,
        )
    )


async def test_semantically_equivalent_answer(tmp_path):
    result = await _judge().judge(
        case={
            "case_id": "answer",
            "input": "What is 12 + 30? Respond in a full sentence.",
            "reference": {"answer": "42"},
        },
        execution_result=CaseExecutionResult("The answer is 42.", "passed"),
        output_dir=str(tmp_path),
    )
    assert result.passed
    assert result.score == 1.0


async def test_partial_rubric_has_separate_scores(tmp_path):
    result = await _judge().judge(
        case={
            "case_id": "rubric",
            "input": "Name Monday and Tuesday.",
            "reference": {"rubric": ["The response names Monday.", "The response names Tuesday."]},
        },
        execution_result=CaseExecutionResult("Monday.", "passed"),
        output_dir=str(tmp_path),
    )
    scores = result.metadata["dimensions"]["per_behavior_scores"]
    assert scores == {"rubric_001": 1.0, "rubric_002": 0.0}
    assert result.score == 0.5
    assert not result.passed


async def test_reads_artifact_instead_of_trusting_completion_claim(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "reference.json").write_text(json.dumps({"expected_total": 30}), encoding="utf-8")
    case_dir = tmp_path / "case"
    (case_dir / "artifacts").mkdir(parents=True)
    artifact = case_dir / "artifacts" / "report.txt"
    artifact.write_text("Total: 12\n", encoding="utf-8")
    result = await _judge().judge(
        case={
            "case_id": "artifact",
            "case_path": str(dataset / "cases.json"),
            "input": "Produce report.txt with the total specified in the reference.",
            "reference": {
                "rubric": ["The numeric total in the delivered report.txt equals expected_total in reference.json."],
                "files": ["reference.json"],
            },
        },
        execution_result=CaseExecutionResult("Completed and verified report.txt: total is 30.", "passed"),
        output_dir=str(case_dir),
    )
    assert result.score == 0.0
    assert not result.passed
    assert artifact.read_text() == "Total: 12\n"
    evidence = result.metadata["parsed"]["behaviors"][0]["evidence"]
    assert "report.txt" in evidence
    tool_log = next((case_dir / "judge").glob("evaluation_*/tool_events.jsonl")).read_text(encoding="utf-8")
    assert "report.txt" in tool_log
