# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prose rubrics reach the existing Judge without inferred weights."""

import copy
import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.data_loader.grading_contract import normalize_grading_case
from openjiuwen.rsi.harness_rsi.data_loader.loader import load_json_cases
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseReader
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger, llm_as_judge
from openjiuwen.rsi.harness_rsi.evaluator.judger.scoring import score_judge_output, scoring_contract
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator
from tests.unit_tests.rsi.test_evaluator import _Backend
from tests.unit_tests.rsi.test_evaluator_agent import _config

RUBRIC = (
    "Total 100 points. Accept equivalent answers.\n"
    "A. Correct result (70 points): answer is 42.\n"
    "B. Explanation (30 points): provide a derivation.\n"
    "Deduct 10 points for invented evidence, without double counting errors."
)


def _case(nested=True):
    if nested:
        return {"id": "prose", "input": "Compute the result.", "reference": {
            "solution": "42", "judge_rubrics": RUBRIC,
        }}
    return {"id": "prose", "input": "Compute the result.",
            "reference_solution": "42", "judge_rubrics": RUBRIC}


def _verdict(score):
    return {
        "status": "completed", "overall_reason": "Applied the supplied rubric.",
        "behaviors": [{"id": "rubric_overall", "score": score,
                       "reason": "Result earns 70, explanation earns 0; no deductions.",
                       "evidence": "Response contains only 42."}],
        "forbidden_hits": [],
    }


@pytest.mark.parametrize("nested", [False, True])
def test_prose_import_preserves_rules_without_extra_answer_weight(tmp_path, nested):
    raw = _case(nested)
    before = copy.deepcopy(raw)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([raw]), encoding="utf-8")
    case = load_json_cases(path)[0]
    assert raw == before
    assert normalize_grading_case(case) == case
    behaviors, forbidden = scoring_contract(case)
    assert behaviors == [{"id": "rubric_overall", "description": RUBRIC, "weight": 1.0}]
    assert forbidden == []
    assert case["reference"]["answer_role"] == "reference"
    assert score_judge_output(_verdict(0.7), behaviors, forbidden)[0] == pytest.approx(0.7)


@pytest.mark.parametrize("text", [
    "1. Correct answer earns full credit.\n2. Otherwise award no credit.",
    "[Correctness] Evaluate the proof, accepting equivalent formulations.",
    "A. Result (70 points).\nB. Evidence (30 points).",
])
def test_prose_does_not_require_percentages_or_reference_answer(text):
    behaviors, forbidden = scoring_contract({"reference": {"judge_rubrics": text}})
    assert behaviors[0]["description"] == text
    assert len(behaviors) == 1 and not forbidden


@pytest.mark.parametrize("text", [None, "", "   ", {}, []])
def test_empty_or_nontext_rubrics_remain_invalid(text):
    with pytest.raises(ValueError, match="non-empty text"):
        normalize_grading_case({"reference": {"judge_rubrics": text}})


def test_malformed_explicit_weight_contract_is_not_silently_reinterpreted():
    with pytest.raises(ValueError, match="sum to 100"):
        normalize_grading_case({"judge_rubrics": "1. [weight 70%] Missing remainder."})


@pytest.mark.parametrize("score", [-0.1, 1.1, True, "0.7", float("nan"), float("inf")])
def test_prose_verdict_still_validates_numeric_range(score):
    behaviors, forbidden = scoring_contract(_case())
    with pytest.raises(ValueError):
        score_judge_output(_verdict(score), behaviors, forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("score,expected", [(0, 0), (0.7, 0), (0.8, 1), (1, 1)])
async def test_prose_judge_preserves_pass_threshold_and_analyzer_evidence(tmp_path, monkeypatch, score, expected):
    judge_call = AsyncMock(return_value=json.dumps(_verdict(score)))
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", judge_call)
    evaluator = TeamEvaluator(_config())
    evaluator.case_runner = CaseRunner(backend=_Backend("42"), judger=LlmAsJudgeJudger(_config()))
    await evaluator.evaluate_batch([_case()], "", "", str(tmp_path / "eval"))
    result = CaseReader.read_case_inputs(str(tmp_path / "eval" / "cases"))[0]
    assert result.evaluation_passed is bool(expected)
    metadata = result.evaluation_metadata
    assert metadata["parsed"]["overall_score"] == score
    assert metadata["requirement_results"]["items"][0]["requirement_id"] == "rubric_overall"
    assert metadata["parsed"]["behaviors"][0]["reason"] == _verdict(score)["behaviors"][0]["reason"]
    workspace = judge_call.call_args.args[1]
    request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
    assert request["rubric_instructions"] == RUBRIC
    assert request["reference_answer"] == "42"
    assert request["behaviors"][0]["description"] == RUBRIC
    assert request["forbidden_behaviors"] == []
