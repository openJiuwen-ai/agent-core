# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Focused coverage for the standalone Harness evaluator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import (
    CaseExecutionResult,
    SingleHarnessExecutionBackend,
    _artifact_files_from_case,
    _is_runtime_workspace_metadata,
    _resolve_skill_dir,
    _single_harness_team_skill_metadata,
    build_backend,
)
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import (
    ExactMatchJudger,
    JudgeResult,
    ScriptBasedJudger,
    build_judger,
)
from openjiuwen.rsi.harness_rsi.evaluator.metrics_collector import MetricsCollector
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator, _load_completed_case_ref
from openjiuwen.rsi.harness_rsi.schema import EvaluationCaseTraceRef


class _Backend:
    def __init__(self, response: Any, *, status: str = "passed") -> None:
        self.response = response
        self.status = status
        self.cleaned: list[tuple[str, str]] = []

    async def execute(
        self,
        *,
        case: dict[str, Any],
        output_dir: str,
        session_id: str,
        team_skill_ref_path: str | Path | None = None,
        harness_refs: dict[str, str] | None = None,
    ) -> CaseExecutionResult:
        del case, session_id, team_skill_ref_path, harness_refs
        workspace = Path(output_dir) / "workspace"
        artifacts = workspace / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "answer.txt").write_text("answer", encoding="utf-8")
        return CaseExecutionResult(
            response=self.response,
            execution_status=self.status,
            error="failed" if self.status != "passed" else "",
            workspace_dir=str(workspace),
            metadata={"member_id": "solver", "member_role": "solver"},
        )

    async def cleanup(self, team_name: str, session_id: str) -> None:
        self.cleaned.append((team_name, session_id))


class _InfrastructureRetryCaseRunner:
    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def execute(self, *, case: dict[str, Any], output_dir: str, **_: Any) -> EvaluationCaseTraceRef:
        self.calls += 1
        if self.errors:
            raise EvaluationInfrastructureError(self.errors.pop(0))
        case_dir = Path(output_dir)
        case_dir.mkdir(parents=True, exist_ok=True)
        result_path = case_dir / "result.json"
        trace_path = case_dir / "trace.json"
        result_path.write_text(
            json.dumps(
                {
                    "case_id": case["case_id"],
                    "status": "passed",
                    "score": 1.0,
                    "evaluation": {"passed": True, "method": "script_based"},
                }
            ),
            encoding="utf-8",
        )
        trace_path.write_text(json.dumps({"case_id": case["case_id"]}), encoding="utf-8")
        return EvaluationCaseTraceRef(
            case_id=case["case_id"],
            case_path="",
            trace_path=str(trace_path),
            result_path=str(result_path),
            status="passed",
            score=1.0,
        )


def test_build_backend_accepts_only_single_harness() -> None:
    backend = build_backend(EvaluatorConfig())
    assert isinstance(backend, SingleHarnessExecutionBackend)
    with pytest.raises(ValueError, match="unknown backend type"):
        build_backend(EvaluatorConfig(backend="local"))


def test_build_judger_supports_deterministic_methods() -> None:
    assert isinstance(build_judger(EvaluatorConfig()), ScriptBasedJudger)
    assert isinstance(
        build_judger(EvaluatorConfig(evaluation_method="exact_match")),
        ExactMatchJudger,
    )
    with pytest.raises(ValueError, match="unsupported evaluation_method"):
        build_judger(EvaluatorConfig(evaluation_method="unknown-judge"))


@pytest.mark.asyncio
async def test_exact_match_judger_scores_response() -> None:
    judger = ExactMatchJudger()
    result = await judger.judge(
        case={"reference": {"answer": "ok"}},
        execution_result=CaseExecutionResult(response="ok", execution_status="passed"),
    )
    assert result.passed is True
    assert result.score == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("judger", [ScriptBasedJudger(), ExactMatchJudger()])
@pytest.mark.parametrize("case", [{}, {"reference": {}}, {"reference": {"expected_files": ["answer.md"]}}])
@pytest.mark.parametrize("status", ["passed", "error"])
async def test_missing_grading_evidence_is_not_a_task_score(judger, case, status) -> None:
    with pytest.raises(EvaluationInfrastructureError, match="cannot score this case"):
        await judger.judge(
            case=case,
            execution_result=CaseExecutionResult(response="done", execution_status=status),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("judger", [ScriptBasedJudger(), ExactMatchJudger()])
async def test_execution_error_with_explicit_reference_is_not_passing(judger) -> None:
    grade = await judger.judge(
        case={"reference": {"answer": "ok"}},
        execution_result=CaseExecutionResult(response="ok", execution_status="error", error="task aborted"),
    )
    assert not grade.passed and grade.score == 0.0
    assert grade.reason == "task aborted"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["script_based", "rule_based", "default", "pass_through", ""])
async def test_default_judge_aliases_cannot_award_completion_credit(method: str) -> None:
    with pytest.raises(EvaluationInfrastructureError, match="no backend JudgeResult or reference answer"):
        await build_judger(EvaluatorConfig(evaluation_method=method)).judge(
            case={}, execution_result=CaseExecutionResult(response="done", execution_status="passed")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("judger", [ScriptBasedJudger(), ExactMatchJudger()])
@pytest.mark.parametrize("answer", ["expected answer", "", 0, False])
@pytest.mark.parametrize("alias", ["answer", "nested_output", "reference_answer", "expected_output"])
async def test_explicit_reference_aliases_and_falsy_answers_are_evaluated(judger, answer, alias) -> None:
    if alias == "answer":
        case = {"reference": {"answer": answer}}
    elif alias == "nested_output":
        case = {"reference": {"expected_output": answer}}
    else:
        case = {alias: answer}
    for response, expected_score in ((answer, 1.0), ("wrong answer", 0.0)):
        result = await judger.judge(
            case=case, execution_result=CaseExecutionResult(response=response, execution_status="passed")
        )
        assert result.score == expected_score
        assert result.passed is (expected_score == 1.0)


@pytest.mark.asyncio
async def test_backend_grader_result_is_preserved_without_reference() -> None:
    grade = JudgeResult(method="custom_verifier", score=0.4, passed=False, reason="two checks failed")
    execution = CaseExecutionResult(response="done", execution_status="passed", judge_result=grade)
    assert await ScriptBasedJudger().judge(case={}, execution_result=execution) is grade
    for judger in (None, ScriptBasedJudger()):
        runner = CaseRunner(backend=_Backend("done"), judger=judger)
        assert await runner._judge(case={}, execution_result=execution, output_dir="") is grade


@pytest.mark.asyncio
@pytest.mark.parametrize("judger", [None, ScriptBasedJudger(), ExactMatchJudger()])
async def test_case_runner_retains_evidence_but_does_not_publish_ungraded_score(tmp_path, judger) -> None:
    backend = _Backend("done")
    directory = tmp_path / "case"
    with pytest.raises(EvaluationInfrastructureError):
        await CaseRunner(backend=backend, judger=judger).execute(
            case={"case_id": "ungraded", "input": "write an answer"}, output_dir=str(directory)
        )
    error = json.loads((directory / "evaluation_error.json").read_text(encoding="utf-8"))
    assert error["score"] is None
    assert error["case_id"] == "ungraded"
    assert (directory / "tr" / "trajectory_events.jsonl").is_file()
    assert (directory / "artifacts" / "answer.txt").is_file()
    assert not (directory / "result.json").exists()
    assert backend.cleaned


@pytest.mark.asyncio
async def test_batch_cannot_summarize_or_retry_ungraded_case(tmp_path) -> None:
    evaluator = TeamEvaluator(EvaluatorConfig(transient_case_retry_limit=3))
    backend = _Backend("done")
    evaluator.case_runner = CaseRunner(backend=backend, judger=ScriptBasedJudger())
    stages = []

    async def sink(stage):
        stages.append(stage)

    directory = tmp_path / "evaluation"
    with pytest.raises(EvaluationInfrastructureError, match="no backend JudgeResult"):
        await evaluator.evaluate_batch(
            cases=[{"case_id": "ungraded"}],
            team_skill_ref_path="",
            harness_refs_path="",
            output_dir=str(directory),
            on_case_stage=sink,
        )
    assert len(backend.cleaned) == 1
    assert len(stages) == 1 and stages[0]["score"] is None
    assert not (directory / "summary.json").exists()
    assert not (directory / "eval_ref.yaml").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,metadata",
    [
        ("none", {}),
        ("script_based", {"rule_engine_status": "backend_completed"}),
    ],
)
async def test_legacy_completion_grade_cannot_reenter_scoring_or_reuse(tmp_path, method, metadata) -> None:
    grade = JudgeResult(method=method, score=1.0, passed=True, metadata=metadata)
    runner = CaseRunner(backend=_Backend("done"))
    with pytest.raises(EvaluationInfrastructureError, match="Completion-only"):
        await runner._judge(
            case={},
            output_dir="",
            execution_result=CaseExecutionResult(response="done", execution_status="passed", judge_result=grade),
        )
    directory = tmp_path / "cases" / "case1"
    directory.mkdir(parents=True)
    result = {
        "case_id": "case1",
        "status": "passed",
        "score": 1.0,
        "evaluation": {"method": method, "passed": True, "metadata": metadata},
    }
    (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (directory / "trace.json").write_text(json.dumps({"case_id": "case1"}), encoding="utf-8")
    assert _load_completed_case_ref(directory, case_id="case1") is None
    with pytest.raises(EvaluationInfrastructureError, match="legacy completion-only"):
        await MetricsCollector().collect(str(directory.parent), str(tmp_path / "summary.json"))
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.asyncio
async def test_case_runner_persists_trace_result_and_artifact(tmp_path: Path) -> None:
    backend = _Backend("ok")
    case_ref = await CaseRunner(backend=backend, judger=ExactMatchJudger()).execute(
        case={
            "case_id": "case-1",
            "input": "solve",
            "reference": {"answer": "ok", "expected_artifacts": ["answer.txt"]},
        },
        output_dir=str(tmp_path / "case"),
        harness_refs={"solver": "harness"},
    )

    result = json.loads(Path(case_ref.result_path).read_text(encoding="utf-8"))
    trace = json.loads(Path(case_ref.trace_path).read_text(encoding="utf-8"))
    assert case_ref.score == 1.0
    assert result["evaluation"]["passed"] is True
    assert trace["behavior_trace"]["normalized_trace_path"]
    assert (tmp_path / "case" / "artifacts" / "answer.txt").read_text(encoding="utf-8") == "answer"
    assert backend.cleaned and backend.cleaned[0][0] == "single_harness"


@pytest.mark.asyncio
async def test_team_evaluator_retries_transient_infrastructure_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _skip_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("openjiuwen.rsi.harness_rsi.evaluator.team_evaluator.asyncio.sleep", _skip_sleep)
    evaluator = TeamEvaluator(EvaluatorConfig(transient_case_retry_limit=1))
    case_runner = _InfrastructureRetryCaseRunner(
        ["RemoteProtocolError: peer closed connection (incomplete chunked read)"]
    )
    evaluator.case_runner = case_runner  # type: ignore[assignment]

    eval_ref = await evaluator.evaluate_batch(
        cases=[{"case_id": "case-1"}],
        team_skill_ref_path="",
        harness_refs_path="",
        output_dir=str(tmp_path / "evaluation"),
    )

    retry_files = list((tmp_path / "evaluation" / "cases").glob("*_transient_retries.json"))
    assert Path(eval_ref).is_file()
    assert case_runner.calls == 2
    assert len(retry_files) == 1
    assert "incomplete chunked read" in retry_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_team_evaluator_does_not_retry_deterministic_infrastructure_error(tmp_path: Path) -> None:
    evaluator = TeamEvaluator(EvaluatorConfig(transient_case_retry_limit=5))
    case_runner = _InfrastructureRetryCaseRunner(["invalid Harness package"])
    evaluator.case_runner = case_runner  # type: ignore[assignment]

    with pytest.raises(EvaluationInfrastructureError, match="invalid Harness package"):
        await evaluator.evaluate_batch(
            cases=[{"case_id": "case-1"}],
            team_skill_ref_path="",
            harness_refs_path="",
            output_dir=str(tmp_path / "evaluation"),
        )

    assert case_runner.calls == 1


@pytest.mark.asyncio
async def test_metrics_collector_aggregates_case_results(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    for index, passed in enumerate((True, False), start=1):
        result_dir = cases_dir / f"c{index}"
        result_dir.mkdir(parents=True)
        (result_dir / "result.json").write_text(
            json.dumps(
                {
                    "status": "passed" if passed else "failed",
                    "score": 1.0 if passed else 0.0,
                    "evaluation": {"passed": passed, "method": "script_based"},
                }
            ),
            encoding="utf-8",
        )

    summary_path = await MetricsCollector().collect(
        str(cases_dir),
        str(tmp_path / "summary.json"),
    )
    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    assert summary["total_cases"] == 2
    assert summary["passed_cases"] == 1
    assert summary["average_score"] == 0.5


def test_skill_ref_resolution_and_metadata(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "fix_contract"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: fix_contract\n---\nDo the work.\n", encoding="utf-8")

    assert _resolve_skill_dir(skill_dir) == skill_dir
    assert _resolve_skill_dir(skill_md) == skill_dir
    metadata = _single_harness_team_skill_metadata(skill_md)
    assert metadata["enabled_skill"] == "fix_contract"
    assert len(metadata["skill_md_sha256"]) == 64


def test_artifact_files_are_derived_from_case_contract() -> None:
    files = _artifact_files_from_case(
        {"reference": {"expected_artifacts": ["report.json", "notes.md"]}},
        "Write index.html and styles.css",
    )
    assert files == ["index.html", "styles.css", "report.json", "notes.md"]


@pytest.mark.parametrize(
    "path",
    [
        "AGENT.md",
        "context/session.json",
        "skills/generated/.workspace",
    ],
)
def test_runtime_workspace_metadata_is_not_harvested(path: str) -> None:
    assert _is_runtime_workspace_metadata(path) is True
