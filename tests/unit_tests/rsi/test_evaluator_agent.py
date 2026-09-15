# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluator-agent scoring, isolation and real CaseRunner/Analyzer contracts."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseReader
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import LlmJudgeSignalExtractor
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import LlmAsJudgeJudger, ScriptBasedJudger, build_judger, llm_as_judge
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_evidence import prepare_judge_workspace
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import JudgeBudgetRail, JudgeReadOnlyRail
from openjiuwen.rsi.harness_rsi.evaluator.judger.scoring import parse_judge_output, score_judge_output, scoring_contract
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator, _evaluation_input_fingerprint
from tests.unit_tests.rsi.test_evaluator import _Backend


def _config(**kwargs):
    return EvaluatorConfig(evaluation_method="llm_as_judge", judge_model_config_ref="mock-model.yaml", **kwargs)


def _case():
    return {
        "case_id": "sample",
        "input": "Deliver the requested report.",
        "reference": {"rubric": ["Include a total", "Include the units"]},
    }


def _output(scores=(1.0, 0.0)):
    return {
        "status": "completed",
        "overall_reason": "One requirement is missing",
        "behaviors": [
            {
                "id": f"rubric_{index:03d}",
                "score": score,
                "reason": "Observed in response",
                "evidence": "response: total is present but no units are stated",
            }
            for index, score in enumerate(scores, 1)
        ],
        "forbidden_hits": [],
    }


def test_explicit_factory_and_config_roundtrip():
    assert EvaluatorConfig().judge_timeout_sec == 900
    assert EvaluatorConfig.from_dict({}).judge_timeout_sec == 900
    assert EvaluatorConfig.from_dict({"judge_timeout_sec": 600}).judge_timeout_sec == 600
    assert EvaluatorConfig().judge_success_score == 0.8
    assert EvaluatorConfig.from_dict({}).judge_success_score == 0.8
    config = EvaluatorConfig.from_dict(
        {
            "evaluation_method": "llm-as-judge",
            "judge_model_config_ref": "judge.yaml",
            "judge_success_score": 0.8,
            "judge_agent_max_iterations": 6,
        }
    )
    assert isinstance(build_judger(config), LlmAsJudgeJudger)
    assert config.judge_success_score == 0.8
    assert config.judge_agent_max_iterations == 6
    assert isinstance(build_judger(EvaluatorConfig()), ScriptBasedJudger)
    with pytest.raises(ValueError, match="model_config_ref"):
        build_judger(EvaluatorConfig(evaluation_method="llm_as_judge"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "score,passed", [(0.0, False), (0.6, False), (0.799, False), (0.8, True), (0.965, True), (1.0, True)],
)
async def test_configured_threshold_reaches_case_reference(tmp_path, monkeypatch, score, passed):
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", AsyncMock(return_value=json.dumps(_output((score, score)))))
    runner = CaseRunner(backend=_Backend("done"), judger=LlmAsJudgeJudger(_config(judge_success_score=0.8)))
    ref = await runner.execute(case=_case(), output_dir=str(tmp_path / "case"), team_skill_ref_path="")
    assert ref.score == float(passed)
    assert ref.metadata["evaluation_passed"] is passed
    assert ref.status == ("passed" if passed else "failed")
    result = json.loads(Path(ref.result_path).read_text(encoding="utf-8"))
    assert result["score"] == float(passed)
    trace = json.loads(Path(ref.trace_path).read_text(encoding="utf-8"))
    assert trace["evaluation"]["score"] == float(passed)
    metadata = result["evaluation"]["metadata"]
    assert metadata["parsed"]["overall_score"] == pytest.approx(score)
    assert metadata["optimization_signals"]["continuous_score"]["value"] == pytest.approx(score)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_scores", [
    [0.53, 1.0, 0.929, 0.815, 1.0],
    [0.845, 1.0, 1.0, 0.734, 0.967],
])
async def test_node_score_averages_binary_cases_not_raw_judge_scores(tmp_path, monkeypatch, raw_scores):
    from openjiuwen.rsi.harness_rsi.single_harness.events_translate import progress_event, root_node_event
    from openjiuwen.rsi.harness_rsi.single_harness.iterative import _eval_case_native_signals, _eval_score

    monkeypatch.setattr(llm_as_judge, "run_judge_agent", AsyncMock(side_effect=[
        json.dumps(_output((score, score))) for score in raw_scores
    ]))
    evaluator = TeamEvaluator(_config())
    evaluator.case_runner = CaseRunner(backend=_Backend("done"), judger=LlmAsJudgeJudger(_config()))
    cases = [{**_case(), "case_id": f"case-{i}"} for i in range(5)]
    stages = []

    async def stage(event):
        stages.append(event)

    ref_path = await evaluator.evaluate_batch(cases, "", "", str(tmp_path / "eval"), on_case_stage=stage)
    summary = json.loads((tmp_path / "eval/summary.json").read_text(encoding="utf-8"))
    assert summary["passed_cases"] == 4
    assert summary["average_score"] == 0.8
    assert _eval_score(ref_path) == 0.8
    assert [s["score"] for s in stages if s.get("status") in {"passed", "failed"}] == [
        float(score >= 0.8) for score in raw_scores
    ]
    state = {"baseline_score": _eval_score(ref_path), "best_score": _eval_score(ref_path)}
    assert root_node_event(state).node.score == 0.8
    progress = progress_event(state, total_iterations=3)
    assert progress.score == progress.baseline == 0.8
    signals = _eval_case_native_signals(ref_path)
    assert len(signals) == 5
    assert [signals[f"case-{i}"]["score"] for i in range(5)] == pytest.approx(raw_scores)


@pytest.mark.parametrize(
    "changes",
    [
        {"judge_success_score": float("nan")},
        {"judge_success_score": 1.1},
        {"judge_agent_max_iterations": 0},
        {"judge_timeout_sec": 0},
        {"judge_max_retries": True},
        {"judge_max_retries": 10},
    ],
)
def test_invalid_judge_settings_are_rejected(changes):
    with pytest.raises(ValueError):
        LlmAsJudgeJudger(replace(_config(), **changes))


def test_answer_rubric_and_legacy_requirements_are_normalized_without_task_heuristics():
    case = _case()
    case["reference"].update(answer="42", required_behaviors=[{"id": "detail", "description": "Explain", "weight": 2}])
    behaviors, forbidden = scoring_contract(case)
    assert [item["id"] for item in behaviors] == ["reference_answer", "detail", "rubric_001", "rubric_002"]
    assert not forbidden
    alternate = {**case, "case_id": "totally-different", "input": "An unrelated domain", "source": "another-benchmark"}
    assert scoring_contract(alternate) == (behaviors, forbidden)


@pytest.mark.parametrize(
    "reference",
    [
        {},
        {"files": ["private.json"]},
        {"rubric": "not a list"},
        {"rubric": [""]},
        {"required_behaviors": ["same", "same"]},
        {"required_behaviors": [{"id": "one", "weight": 0}]},
    ],
)
def test_missing_or_invalid_contract_fails_preflight(reference):
    with pytest.raises(EvaluationInfrastructureError):
        LlmAsJudgeJudger(_config()).validate_case({"input": "Task", "reference": reference})


@pytest.mark.parametrize("field", ["id", "description"])
@pytest.mark.parametrize("value", [None, "", "   ", 42, False, []])
def test_invalid_behavior_identity_is_a_validation_error(field, value):
    item = {"id": "criterion", "description": "Check the response", field: value}
    with pytest.raises(ValueError, match=f"behavior {field} must be a non-empty string"):
        scoring_contract({"reference": {"required_behaviors": [item]}})


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "extra",
        "duplicate",
        "nan",
        "string_score",
        "bool_score",
        "negative",
        "too_large",
        "no_reason",
        "no_evidence",
    ],
)
def test_model_cannot_omit_requirements_or_invent_valid_scores(defect):
    parsed = _output()
    item = parsed["behaviors"][0]
    if defect == "missing":
        parsed["behaviors"].pop()
    elif defect == "extra":
        parsed["behaviors"].append({**item, "id": "extra"})
    elif defect == "duplicate":
        parsed["behaviors"].append(dict(item))
    elif defect == "no_reason":
        item.pop("reason")
    elif defect == "no_evidence":
        item.pop("evidence")
    else:
        item["score"] = {"nan": float("nan"), "string_score": "1", "bool_score": True, "negative": -1, "too_large": 2}[
            defect
        ]
    with pytest.raises(ValueError):
        score_judge_output(parsed, *scoring_contract(_case()))


def test_weights_and_forbidden_penalties_are_from_reference_not_model():
    case = {
        "reference": {
            "required_behaviors": [{"id": "a", "weight": 3}, {"id": "b", "weight": 1}],
            "forbidden_behaviors": [{"id": "unsafe", "penalty": 0.3}],
        }
    }
    output = {
        "overall_reason": "Observed defects",
        "overall_score": 1.0,
        "behaviors": [
            {"id": "a", "score": 1.0, "weight": 100, "reason": "good", "evidence": "response"},
            {"id": "b", "score": 0.0, "weight": 0, "reason": "bad", "evidence": "response"},
        ],
        "forbidden_hits": [
            {"id": "unsafe", "triggered": True, "penalty": 0, "reason": "present", "evidence": "response"}
        ],
    }
    score, parsed, requirements = score_judge_output(output, *scoring_contract(case))
    assert score == pytest.approx(0.7)
    assert parsed["forbidden_hits"][0]["penalty"] == 0.3
    assert len(requirements["items"]) == 3
    output["behaviors"][0]["score"] = 0.2
    assert score_judge_output(output, *scoring_contract(case))[0] == pytest.approx(0.15)


def test_json_parser_handles_braces_in_strings_and_fences():
    value = _output()
    value["overall_reason"] = 'A literal "{" is not a new object'
    assert parse_judge_output("```json\n" + json.dumps(value) + "\n```") == value
    assert parse_judge_output("I reviewed the evidence.\n```json\n" + json.dumps(value) + "\n```\nEnd.") == value
    with pytest.raises(ValueError):
        parse_judge_output("Here is my assessment: " + json.dumps(value))


@pytest.mark.parametrize("raw", [
    '```json\n{}\n```\n```json\n{}\n```',
    'Before {}\n```json\n{}\n```',
    '```json\n{}\n```\nAfter {}',
    '```json\n{"status": "completed"',
    '{"score": 0, "score": 1}',
    '{"behaviors": [{"score": 0, "score": 1}]}',
    '{"score": NaN}',
    '```python\n{}\n```',
    '[]',
    '<tool_calls><invoke name="read_file" /></tool_calls>',
])
def test_judge_parser_does_not_guess_or_choose_among_ambiguous_verdicts(raw):
    with pytest.raises(ValueError):
        parse_judge_output(raw)


def test_snapshot_contains_declared_evidence_not_config_or_old_grades(tmp_path):
    case = _case()
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "private.txt").write_text("trusted reference", encoding="utf-8")
    case["case_path"] = str(dataset / "cases.json")
    case["reference"]["files"] = ["private.txt"]
    root = tmp_path / "case"
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "report.txt").write_text("actual work", encoding="utf-8")
    (root / "secret.yaml").write_text("not judge evidence", encoding="utf-8")
    (root / "result.json").write_text("old grade", encoding="utf-8")
    workspace = root / "judge" / "isolated"
    prepare_judge_workspace(
        case=case,
        response="done",
        case_dir=root,
        workspace=workspace,
        behaviors=scoring_contract(case)[0],
        forbidden=[],
    )
    assert (workspace / "reference" / "private.txt").read_text() == "trusted reference"
    assert (workspace / "artifacts" / "report.txt").read_text() == "actual work"
    assert not (workspace / "secret.yaml").exists()
    assert not (workspace / "result.json").exists()
    (workspace / "artifacts" / "report.txt").write_text("edited snapshot")
    assert (root / "artifacts" / "report.txt").read_text() == "actual work"


def test_snapshot_rejects_external_symlinks(tmp_path):
    root = tmp_path / "case"
    (root / "artifacts").mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("not evidence")
    try:
        (root / "artifacts" / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="escaping"):
        prepare_judge_workspace(
            case=_case(),
            response="done",
            case_dir=root,
            workspace=root / "judge" / "isolated",
            behaviors=scoring_contract(_case())[0],
            forbidden=[],
        )


@pytest.mark.asyncio
async def test_case_runner_to_analyzer_preserves_all_criteria(tmp_path, monkeypatch):
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
        _build_diagnosis_input_json,
        _build_diagnosis_prompt,
    )
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import build_signal_extractor

    output = _output()
    output["behaviors"][0].update(reason="A numeric total is present", evidence="response: total: 30")
    output["behaviors"][1].update(reason="No units accompany the total", evidence="response ends immediately after 30")
    judge_call = AsyncMock(return_value=json.dumps(output))
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", judge_call)
    evaluator = TeamEvaluator(_config())
    evaluator.case_runner = CaseRunner(backend=_Backend("total: 30"), judger=LlmAsJudgeJudger(_config()))
    await evaluator.evaluate_batch([_case()], "", "", str(tmp_path / "eval"))
    cases = CaseReader.read_case_inputs(str(tmp_path / "eval" / "cases"))
    summary = CaseReader.read_summary(str(tmp_path / "eval" / "summary.json"))
    extractor = build_signal_extractor(summary.evaluation_method)
    assert isinstance(extractor, LlmJudgeSignalExtractor)
    signals = extractor.extract(summary, cases)
    assert cases[0].score == 0.0
    assert signals.method_specific["low_score_behaviors"]["sample"] == ["rubric_002"]
    assert len(cases[0].evaluation_metadata["requirement_results"]["items"]) == 2
    for evidence_available in (False, True):
        arguments = {
            "case": cases[0], "signals": signals, "retrieved_experience": None,
            "evidence_summary_available": evidence_available,
        }
        raw_input = _build_diagnosis_input_json(**arguments)
        payload = json.loads(raw_input)
        breakdown = payload["case_facts"]["judge_breakdown"]
        assert breakdown["overall_reason"] == output["overall_reason"]
        assert breakdown["overall_score"] == 0.5
        assert payload["case_facts"]["evaluation_passed"] is False
        assert len(breakdown["behaviors"]) == len(output["behaviors"])
        for expected, actual in zip(output["behaviors"], breakdown["behaviors"]):
            for field in ("id", "score", "reason", "evidence"):
                assert actual[field] == expected[field]
        assert breakdown["dimensions"]["low_score_behaviors"] == ["rubric_002"]
        # Verify the model-facing prompt, not only the intermediate signal extractor.
        assert raw_input in _build_diagnosis_prompt(**arguments)
    judge_call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unavailable", "invalid_json", "missing_criterion", "model_error"])
async def test_judge_failures_do_not_become_zero_score_tasks(tmp_path, monkeypatch, kind):
    if kind == "model_error":
        call = AsyncMock(side_effect=RuntimeError("authentication failed"))
    else:
        output = (
            {"status": "unavailable", "reason": "rendering is not available"} if kind == "unavailable" else _output()
        )
        if kind == "missing_criterion":
            output["behaviors"].pop()
        call = AsyncMock(return_value="not JSON" if kind == "invalid_json" else json.dumps(output))
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", call)
    runner = CaseRunner(backend=_Backend("done"), judger=LlmAsJudgeJudger(_config()))
    with pytest.raises(EvaluationInfrastructureError):
        await runner.execute(case=_case(), output_dir=str(tmp_path / "case"), team_skill_ref_path="")
    assert json.loads((tmp_path / "case" / "evaluation_error.json").read_text())["score"] is None
    assert not (tmp_path / "case" / "result.json").exists()
    assert call.await_count == (1 if kind == "model_error" else 2)


@pytest.mark.asyncio
async def test_missing_delivery_is_zero_and_batch_continues_to_next_case(tmp_path, monkeypatch):
    from openjiuwen.rsi.harness_rsi.single_harness.iterative import _nonpassing_case_ids

    missing = _output((0.0, 0.0))
    missing["overall_reason"] = "The final response claims success but supplies no deliverable."
    for item in missing["behaviors"]:
        item.update(reason="Required work was not delivered", evidence="response: I completed it above; no artifacts")
    call = AsyncMock(side_effect=[
        json.dumps({"status": "unavailable", "reason": "The agent supplied only a summary, not the deliverable"}),
        json.dumps(missing),
        json.dumps(_output((1.0, 1.0))),
    ])
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", call)
    evaluator = TeamEvaluator(_config())
    evaluator.case_runner = CaseRunner(backend=_Backend("I completed it above"), judger=LlmAsJudgeJudger(_config()))
    ref_path = await evaluator.evaluate_batch(
        [_case(), {**_case(), "case_id": "next"}], "", "", str(tmp_path / "eval"),
    )
    assert _nonpassing_case_ids(ref_path) == {_case()["case_id"]}
    cases = CaseReader.read_case_inputs(str(tmp_path / "eval/cases"))
    assert [case.score for case in cases] == [0.0, 1.0]
    assert cases[0].evaluation_metadata["parsed"]["overall_reason"] == missing["overall_reason"]
    assert cases[0].evaluation_metadata["requirement_results"]["items"]
    assert call.call_args_list[0].args[1] == call.call_args_list[1].args[1]
    assert "task failures" in call.call_args_list[1].args[2]
    assert not list((tmp_path / "eval").rglob("evaluation_error.json"))


@pytest.mark.asyncio
async def test_one_structural_retry_uses_same_frozen_evidence(tmp_path, monkeypatch):
    workspaces = []
    prompts = []

    async def run(_config, workspace, _prompt, _log):
        workspaces.append(workspace)
        prompts.append(_prompt)
        return "not JSON" if len(workspaces) == 1 else json.dumps(_output())

    monkeypatch.setattr(llm_as_judge, "run_judge_agent", run)
    result = await LlmAsJudgeJudger(_config()).judge(
        case=_case(), execution_result=CaseExecutionResult("done", "passed"), output_dir=str(tmp_path)
    )
    assert workspaces[0] == workspaces[1]
    assert result.metadata["attempt"] == 2
    assert result.score == 0.0
    assert result.metadata["parsed"]["overall_score"] == 0.5
    assert "not JSON" in prompts[1]
    assert "prior_output" in prompts[1]
    errors = list(tmp_path.rglob("validation_error_1.json"))
    assert len(errors) == 1
    assert json.loads(errors[0].read_text(encoding="utf-8"))["message"]


@pytest.mark.asyncio
async def test_tool_text_then_prose_wrapped_verdict_is_recovered_without_changing_score(tmp_path, monkeypatch):
    call = AsyncMock(side_effect=[
        '<tool_calls><invoke name="read_file" /></tool_calls>',
        "Evidence reviewed.\n```json\n" + json.dumps(_output((0.0, 0.0))) + "\n```",
    ])
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", call)
    result = await LlmAsJudgeJudger(_config()).judge(
        case=_case(), execution_result=CaseExecutionResult("done", "passed"), output_dir=str(tmp_path)
    )
    assert result.score == 0.0
    assert result.passed is False
    assert call.await_count == 2


@pytest.mark.asyncio
async def test_final_validation_error_remains_actionable(tmp_path, monkeypatch):
    output = _output()
    output["behaviors"].pop()
    call = AsyncMock(return_value=json.dumps(output))
    monkeypatch.setattr(llm_as_judge, "run_judge_agent", call)
    with pytest.raises(EvaluationInfrastructureError, match="must score every supplied ID"):
        await LlmAsJudgeJudger(_config()).judge(
            case=_case(), execution_result=CaseExecutionResult("done", "passed"), output_dir=str(tmp_path)
        )
    assert call.await_count == 2
    assert len(list(tmp_path.rglob("validation_error_*.json"))) == 2


@pytest.mark.asyncio
async def test_timeout_cancels_agent_instead_of_fabricating_score(tmp_path, monkeypatch):
    async def run(*_args):
        await asyncio.sleep(10)

    monkeypatch.setattr(llm_as_judge, "run_judge_agent", run)
    with pytest.raises(EvaluationInfrastructureError):
        await LlmAsJudgeJudger(_config(judge_timeout_sec=1)).judge(
            case=_case(), execution_result=CaseExecutionResult("done", "passed"), output_dir=str(tmp_path)
        )


@pytest.mark.asyncio
async def test_budget_reserves_final_turn():
    rail = JudgeBudgetRail(2, Path("unused.jsonl"))
    ctx = SimpleNamespace(
        extra={}, inputs=SimpleNamespace(tools=["read_file"]), context=SimpleNamespace(add_messages=AsyncMock())
    )
    await rail.before_model_call(ctx)
    assert ctx.inputs.tools
    await rail.before_model_call(ctx)
    assert ctx.inputs.tools is None
    ctx.context.add_messages.assert_awaited_once()


def test_read_only_rail_does_not_register_execution_or_write_tools():
    added = []
    agent = SimpleNamespace(
        card=SimpleNamespace(id="judge"),
        system_prompt_builder=SimpleNamespace(language="en"),
        ability_manager=SimpleNamespace(add_ability=lambda card, tool: added.append(type(tool).__name__)),
    )
    rail = JudgeReadOnlyRail()
    rail.init(agent)
    assert set(added) == {"ReadFileTool", "ListDirTool", "GlobTool", "GrepTool"}


def test_judge_model_or_threshold_changes_invalidate_reused_evaluations(tmp_path):
    model = tmp_path / "model.json"
    model.write_text('{"model": "first"}')
    config = replace(_config(), judge_model_config_ref=str(model))
    kwargs = {"cases": [_case()], "team_skill_ref_path": "", "harness_refs_path": "", "evaluator_config": config}
    before = _evaluation_input_fingerprint(**kwargs)
    model.write_text('{"model": "second"}')
    assert _evaluation_input_fingerprint(**kwargs) != before
    assert _evaluation_input_fingerprint(**kwargs) != _evaluation_input_fingerprint(
        **{**kwargs, "evaluator_config": replace(config, judge_success_score=0.9)}
    )
