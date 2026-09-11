# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Opt-in real-model checks for the generic evaluator agent, without a task-agent run."""

import json
import os
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.data_loader.loader import load_json_cases
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _build_diagnosis_input_json
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseReader
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import build_signal_extractor
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator

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


async def test_weighted_rubric_and_two_deductions(tmp_path):
    result = await _judge().judge(
        case={
            "id": "weighted",
            "input": "Name Monday and Tuesday. Do not mention Friday or Sunday.",
            "reference_solution": "Monday and Tuesday.",
            "judge_rubrics": (
                "1. [weight 80%] The response names Monday.\n"
                "2. [weight 20%] The response names Tuesday.\n"
                "3. [deduct 6%] The response mentions Friday.\n"
                "4. [deduct 4%] The response mentions Sunday."
            ),
        },
        execution_result=CaseExecutionResult("Monday, Friday, Sunday.", "passed"),
        output_dir=str(tmp_path),
    )
    assert result.score == pytest.approx(0.7)
    parsed = result.metadata["parsed"]
    assert parsed["base_score"] == pytest.approx(0.8)
    assert parsed["total_deduction"] == pytest.approx(0.1)
    assert len(parsed["behaviors"]) == 2
    assert all(hit["triggered"] for hit in parsed["forbidden_hits"])
    assert len(result.metadata["requirement_results"]["items"]) == 4


@pytest.mark.skipif(not os.getenv("RSI_JUDGE_LIVE_DATASET_ZIP"), reason="requires an explicit weighted dataset ZIP")
async def test_uploaded_weighted_rubric_to_analyzer(tmp_path):
    # Read only cases.json, without extracting arbitrary archive paths.
    with zipfile.ZipFile(os.environ["RSI_JUDGE_LIVE_DATASET_ZIP"]) as archive:
        entries = [item for item in archive.namelist() if item == "cases.json" or item.endswith("/cases.json")]
        assert len(entries) == 1
        raw = json.loads(archive.read(entries[0]).decode("utf-8"))
    source = tmp_path / "cases.json"
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cases = load_json_cases(source)
    assert len(cases) == 1
    case = cases[0]
    reference = case["reference"]
    assert reference["answer_role"] == "reference"
    config = EvaluatorConfig(
        evaluation_method="llm_as_judge",
        judge_model_config_ref=MODEL_CONFIG,
        judge_agent_max_iterations=8,
        judge_timeout_sec=240,
        judge_max_retries=0,
    )
    # This checks grading of a supplied response, not task-agent or optimization success.
    backend = SimpleNamespace(
        execute=AsyncMock(return_value=CaseExecutionResult(reference["answer"], "passed")),
        cleanup=AsyncMock(),
    )
    evaluator = TeamEvaluator(config)
    evaluator.case_runner = CaseRunner(backend=backend, judger=LlmAsJudgeJudger(config))
    await evaluator.evaluate_batch(cases, "", "", str(tmp_path / "eval"))
    inputs = CaseReader.read_case_inputs(str(tmp_path / "eval" / "cases"))
    summary = CaseReader.read_summary(str(tmp_path / "eval" / "summary.json"))
    signals = build_signal_extractor(summary.evaluation_method).extract(summary, inputs)
    parsed = inputs[0].evaluation_metadata["parsed"]
    assert len(parsed["behaviors"]) == len(reference["required_behaviors"])
    assert len(parsed["forbidden_hits"]) == len(reference["forbidden_behaviors"])
    assert all(item["id"] != "reference_answer" for item in parsed["behaviors"])
    positive = sum(item["score"] * item["weight"] for item in parsed["behaviors"])
    deductions = sum(item["penalty"] for item in parsed["forbidden_hits"] if item["triggered"])
    assert inputs[0].score == pytest.approx(max(0, positive - deductions))
    payload = json.loads(
        _build_diagnosis_input_json(
            case=inputs[0],
            signals=signals,
            retrieved_experience=None,
            evidence_summary_available=True,
        )
    )
    (tmp_path / "analyzer_input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    breakdown = payload["case_facts"]["judge_breakdown"]
    assert len(breakdown["behaviors"]) == len(parsed["behaviors"])
    assert breakdown["forbidden_hits"] == parsed["forbidden_hits"]
    for actual, expected in zip(breakdown["behaviors"], parsed["behaviors"]):
        for field in ("id", "score", "weight", "description", "evidence"):
            assert actual[field] == expected[field]
        assert actual["reason"]
