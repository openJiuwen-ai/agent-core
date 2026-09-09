# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Weighted rubric import, cumulative deductions and Analyzer handoff."""

import copy
import json
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.data_loader.grading_contract import normalize_grading_case, parse_weighted_rubric
from openjiuwen.rsi.harness_rsi.data_loader.loader import load_json_cases
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _build_diagnosis_input_json
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseReader
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import build_signal_extractor
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger, llm_as_judge
from openjiuwen.rsi.harness_rsi.evaluator.judger.scoring import score_judge_output, scoring_contract
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator
from tests.unit_tests.rsi.test_evaluator import _Backend
from tests.unit_tests.rsi.test_evaluator_agent import _config

RUBRIC = (
    "Grade the response against the following criteria.\n"
    "## Positive requirements\n"
    "1. [weight 80%] Name Monday.\n"
    "2. [weight 20%] Name Tuesday.\n"
    "## Deductions\n"
    "3. [deduct 6%] Mentions Friday.\n"
    "4. [deduct 4%] Mentions Sunday.\n"
)


def _case(nested=False):
    case = {
        "id": "example",
        "input": "Name Monday and Tuesday.",
        "reference_solution": "Monday and Tuesday.",
        "judge_rubrics": RUBRIC,
    }
    if nested:
        case["reference"] = {
            "solution": case.pop("reference_solution"),
            "judge_rubrics": case.pop("judge_rubrics"),
        }
    return case


def _verdict():
    return {
        "overall_reason": "Monday present, Tuesday absent; Friday and Sunday both present.",
        "behaviors": [
            {"id": "rubric_001", "score": 1, "reason": "Monday present", "evidence": "response word 1"},
            {"id": "rubric_002", "score": 0, "reason": "Tuesday absent", "evidence": "complete response"},
        ],
        "forbidden_hits": [
            {"id": "deduction_001", "triggered": True, "reason": "Friday present", "evidence": "response word 2"},
            {"id": "deduction_002", "triggered": True, "reason": "Sunday present", "evidence": "response word 3"},
        ],
    }


@pytest.mark.parametrize("nested", [False, True])
def test_alias_import_is_idempotent_and_does_not_mutate_or_double_score_answer(tmp_path, nested):
    raw = _case(nested)
    before = copy.deepcopy(raw)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([raw]), encoding="utf-8")
    case = load_json_cases(path)[0]
    assert raw == before
    assert case["case_id"] == "example"
    assert case["reference"]["answer"] == _case()["reference_solution"]
    assert case["reference"]["answer_role"] == "reference"
    assert case["reference"]["penalty_mode"] == "subtract"
    assert normalize_grading_case(case) == case
    assert scoring_contract(case) == scoring_contract(raw)
    behaviors, forbidden = scoring_contract(case)
    assert [item["id"] for item in behaviors] == ["rubric_001", "rubric_002"]
    assert [item["weight"] for item in behaviors] == [0.8, 0.2]
    assert [item["penalty"] for item in forbidden] == [0.06, 0.04]


def test_chinese_annotations_and_multiline_descriptions_preserve_meaning():
    text = (
        "\u3010\u6b63\u5411\u8bc4\u5206\u9879\u3011\n"
        "1. [\u6743\u91cd60%] First criterion.\nContinuation with detailed evidence.\n"
        "2. [\u6743\u91cd40%] Second criterion.\n"
        "\u3010\u8d1f\u5411\u6263\u5206\u9879\u3011\n"
        "3. [\u62636%] First defect.\n4. [\u6263\u52064%] Second defect."
    )
    positive, negative = parse_weighted_rubric(text)
    assert len(positive) == len(negative) == 2
    assert positive[0]["description"] == "First criterion.\nContinuation with detailed evidence."
    assert positive[1]["description"] == "Second criterion."
    assert [item["penalty"] for item in negative] == [0.06, 0.04]


def test_weighted_groups_keep_subitems_without_rescaling_or_extra_criteria():
    rubric = (
        "Use partial credit for correct steps.\n"
        "## Content\n[weight 90%] Score the following out of 100.\n"
        "1. [60 points] First requirement.\n2. [40 points] Second requirement.\n"
        "## Presentation\n[weight 10%] Score out of ten.\na. First check (5 points).\nb. Second check (5 points)."
    )
    positive, negative = parse_weighted_rubric(rubric)
    assert [item["weight"] for item in positive] == [0.9, 0.1]
    assert "1. [60 points]" in positive[0]["description"]
    assert "2. [40 points]" in positive[0]["description"]
    assert "b. Second check" in positive[1]["description"]
    assert not negative
    with pytest.raises(ValueError, match="unrecognized"):
        parse_weighted_rubric(rubric + "\n3. [deduct six points] Malformed deduction.")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "free form without weights",
        "1. [weight 90%] Missing ten percent",
        RUBRIC + "5. [deduct six points] Ambiguous",
        RUBRIC + "5. Unweighted requirement",
        "1. [weight 0%] Zero",
        "1. [weight 101%] Too large",
        RUBRIC + "5. [deduct 101%] Too large",
        RUBRIC.replace("weight 80%", "weight -80%"),
    ],
)
def test_invalid_or_incomplete_text_is_not_silently_guessed(text):
    with pytest.raises(ValueError):
        parse_weighted_rubric(text)


@pytest.mark.parametrize(
    "reference",
    [
        {"answer": "conflicts with solution"},
        {"rubric": ["conflicting equal-weight rubric"]},
        {"penalty_mode": "ceiling"},
        {"answer_role": "criterion"},
        {"required_behaviors": []},
    ],
)
def test_conflicting_alias_and_canonical_fields_are_rejected(reference):
    with pytest.raises(ValueError, match="conflict|combined"):
        normalize_grading_case({**_case(), "reference": reference})


def test_answer_only_alias_retains_answer_scoring():
    case = {"id": "answer", "input": "Question", "reference_solution": "Answer"}
    behaviors, forbidden = scoring_contract(case)
    assert [item["id"] for item in behaviors] == ["reference_answer"]
    assert not forbidden


@pytest.mark.parametrize("field,value", [("solution", "Different answer"), ("judge_rubrics", "Different rubric")])
def test_conflicting_top_level_and_nested_aliases_are_rejected(field, value):
    with pytest.raises(ValueError, match="conflicting grading fields"):
        normalize_grading_case({**_case(), "reference": {field: value}})


def test_matching_top_level_and_nested_aliases_do_not_duplicate_criteria():
    case = {**_case(), "reference": _case(True)["reference"]}
    assert scoring_contract(case) == scoring_contract(_case())


def test_nested_answer_only_and_canonical_conflict():
    assert scoring_contract({"reference": {"solution": "Answer"}})[0][0]["id"] == "reference_answer"
    with pytest.raises(ValueError, match="conflicting grading fields"):
        normalize_grading_case({"reference": {"solution": "Answer", "answer": "Other"}})


def test_deduction_only_rubric_uses_answer_as_base_not_an_empty_score():
    case = {**_case(), "judge_rubrics": "1. [deduct 6%] Invented evidence."}
    behaviors, forbidden = scoring_contract(case)
    assert [item["id"] for item in behaviors] == ["reference_answer"]
    assert forbidden[0]["penalty"] == 0.06
    with pytest.raises(ValueError, match="requires"):
        scoring_contract({"input": "Question", "judge_rubrics": case["judge_rubrics"]})


def test_cumulative_deductions_use_trusted_weights_and_clamp_at_zero():
    behaviors, forbidden = scoring_contract(_case())
    raw = _verdict()
    raw["overall_score"] = 1.0
    raw["behaviors"][0]["weight"] = 1000000
    raw["forbidden_hits"][0]["penalty"] = 0
    score, parsed, requirements = score_judge_output(raw, behaviors, forbidden, penalty_mode="subtract")
    assert score == pytest.approx(0.7)
    assert parsed["base_score"] == pytest.approx(0.8)
    assert parsed["total_deduction"] == pytest.approx(0.1)
    assert parsed["behaviors"][0]["weight"] == 0.8
    assert parsed["forbidden_hits"][0]["penalty"] == 0.06
    assert len(requirements["items"]) == 4
    raw["behaviors"][0]["score"] = 0.05
    assert score_judge_output(raw, behaviors, forbidden, penalty_mode="subtract")[0] == 0.0


def test_legacy_ceiling_and_equal_weight_contract_are_unchanged():
    behaviors, forbidden = scoring_contract(_case())
    assert score_judge_output(_verdict(), behaviors, forbidden)[0] == pytest.approx(0.8)
    case = {"reference": {"answer": "Answer", "rubric": ["Check one", "Check two"]}}
    assert [item["id"] for item in scoring_contract(case)[0]] == ["reference_answer", "rubric_001", "rubric_002"]


@pytest.mark.parametrize("field,value", [("answer_role", "unknown"), ("penalty_mode", "unknown")])
def test_invalid_scoring_policy_fails_preflight(field, value):
    with pytest.raises(ValueError, match=field):
        scoring_contract({"reference": {"answer": "Answer", field: value}})


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_imported_weighted_judger_result_reaches_analyzer_with_deductions(tmp_path, monkeypatch, nested):
    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps([_case(nested)]), encoding="utf-8")
    cases = load_json_cases(dataset)
    judge_call = AsyncMock(return_value=json.dumps(_verdict()))
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", judge_call)
    evaluator = TeamEvaluator(_config())
    evaluator.case_runner = CaseRunner(backend=_Backend("Monday Friday Sunday"), judger=LlmAsJudgeJudger(_config()))
    await evaluator.evaluate_batch(cases, "", "", str(tmp_path / "eval"))
    case_inputs = CaseReader.read_case_inputs(str(tmp_path / "eval" / "cases"))
    summary = CaseReader.read_summary(str(tmp_path / "eval" / "summary.json"))
    signals = build_signal_extractor(summary.evaluation_method).extract(summary, case_inputs)
    assert case_inputs[0].score == 0.0
    assert case_inputs[0].evaluation_metadata["parsed"]["overall_score"] == pytest.approx(0.7)
    assert signals.method_specific["triggered_forbidden_behaviors"]["example"] == ["deduction_001", "deduction_002"]
    for evidence_available in (False, True):
        prompt_input = json.loads(
            _build_diagnosis_input_json(
                case=case_inputs[0],
                signals=signals,
                retrieved_experience=None,
                evidence_summary_available=evidence_available,
            )
        )
        breakdown = prompt_input["case_facts"]["judge_breakdown"]
        assert breakdown["penalty_mode"] == "subtract"
        assert breakdown["total_deduction"] == pytest.approx(0.1)
        assert [item["weight"] for item in breakdown["behaviors"]] == [0.8, 0.2]
        assert len(breakdown["forbidden_hits"]) == 2
        for actual, expected in zip(breakdown["forbidden_hits"], _verdict()["forbidden_hits"]):
            for field in ("id", "triggered", "reason", "evidence"):
                assert actual[field] == expected[field]
            assert actual["description"]
    workspace = judge_call.call_args.args[1]
    request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
    assert request["reference_answer_role"] == "reference"
    assert request["rubric_instructions"] == RUBRIC
    assert request["reference_answer"] == _case()["reference_solution"]
    assert len(request["behaviors"]) == 2
