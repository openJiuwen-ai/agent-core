# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Parallel evaluation exercises real case persistence, grading, usage and resume."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from openjiuwen.agent_teams.paths import (
    get_openjiuwen_home,
    reset_task_openjiuwen_home,
    set_task_openjiuwen_home,
)
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig, OrchestratorSchedulingConfig
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import ExactMatchJudger
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator
from openjiuwen.rsi.harness_rsi.single_harness.events_translate import case_stage_payload
from openjiuwen.rsi.usage import ModelUsageObserver, record_model_usage, set_usage_node


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_case_isolation_progress_metrics_and_resume(tmp_path, monkeypatch, concurrency):
    active = set()
    started = []
    finished = []
    homes = {}
    runners = []
    peak = 0
    stages = []
    usage_events = []
    others_finished = asyncio.Event()
    parent_home = get_openjiuwen_home()
    outer_token = set_task_openjiuwen_home(tmp_path / "outer")

    class Backend:
        async def execute(self, *, case, output_dir, session_id, harness_refs, **kwargs):
            nonlocal peak
            case_id = case["case_id"]
            active.add(case_id)
            peak = max(peak, len(active))
            started.append(case_id)
            home = get_openjiuwen_home()
            homes[case_id] = home
            assert harness_refs == {"solver": "frozen"}
            harness_refs["solver"] = case_id
            case["reference"]["private_mutation"] = case_id
            try:
                if case_id == "a" and concurrency > 1:
                    await asyncio.wait_for(others_finished.wait(), timeout=10)
                else:
                    await asyncio.sleep(0)
                assert get_openjiuwen_home() == home
                assert await asyncio.to_thread(get_openjiuwen_home) == home
                workspace = Path(output_dir) / "workspace"
                (workspace / "artifacts").mkdir(parents=True)
                (workspace / "artifacts" / "answer.txt").write_text(case_id, encoding="utf-8")
                await record_model_usage(
                    model="task-probe", call_id=session_id, usage={"input_tokens": 5, "output_tokens": 2},
                    stage_ref="evaluate",
                )
                return CaseExecutionResult(
                    response=case_id, execution_status="passed", workspace_dir=str(workspace),
                )
            finally:
                active.remove(case_id)

        async def cleanup(self, team_name, session_id):
            assert get_openjiuwen_home() in homes.values()

    class Judger(ExactMatchJudger):
        async def judge(self, *, case, execution_result, output_dir=""):
            assert get_openjiuwen_home() == homes[case["case_id"]]
            await record_model_usage(
                model="judge-probe", call_id=f"judge-{case['case_id']}",
                usage={"input_tokens": 3, "output_tokens": 1}, stage_ref="judge",
            )
            result = await super().judge(case=case, execution_result=execution_result, output_dir=output_dir)
            finished.append(case["case_id"])
            if case["case_id"] == "c":
                others_finished.set()
            return result

    def make_runner():
        runner = CaseRunner(backend=Backend(), judger=Judger())
        runners.append(runner)
        return runner

    async def on_stage(payload):
        await asyncio.sleep(0)
        stages.append(payload)

    async def on_usage(event):
        usage_events.append(event)

    evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
    monkeypatch.setattr(evaluator, "_make_case_runner", make_runner)
    evaluator.case_runner = make_runner()
    cases = [{"case_id": item, "input": item, "reference": {"answer": item if item != "c" else "wrong"}}
             for item in "abc"]
    refs = tmp_path / "harness_refs.yaml"
    refs.write_text("harness_refs:\n  solver: frozen\n", encoding="utf-8")
    state = {"task_id": "parallel-test"}
    kwargs = {"cases": cases, "team_skill_ref_path": "", "harness_refs_path": str(refs),
              "output_dir": str(tmp_path / "eval"), "case_concurrency": concurrency, "on_case_stage": on_stage}
    try:
        async with ModelUsageObserver(on_usage).observe() as observer:
            await observer.bind(state, tmp_path)
            set_usage_node("h0")
            result = await evaluator.evaluate_batch(**kwargs)
            assert get_openjiuwen_home() == tmp_path / "outer"
            assert peak == concurrency
            assert len(set(homes.values())) == 3
            assert len(runners) == (4 if concurrency > 1 else 1)
            assert not any("private_mutation" in case["reference"] for case in cases)
            if concurrency > 1:
                assert finished[0] == "b" and finished[-1] == "a"
                counts = [stage["completed_cases"] for stage in stages]
                assert counts == sorted(counts) and counts[-1] == 3
                assert next(stage for stage in stages if stage["status"] == "passed")["case_index"] == 2
            evaluation = yaml.safe_load(Path(result).read_text(encoding="utf-8"))
            assert [case["case_id"] for case in evaluation["cases"]] == list("abc")
            assert [case["score"] for case in evaluation["cases"]] == [1.0, 1.0, 0.0]
            summary = json.loads((tmp_path / "eval" / "summary.json").read_text(encoding="utf-8"))
            assert summary["average_score"] == pytest.approx(2 / 3)
            for case in evaluation["cases"]:
                trace = Path(case["trace_path"])
                assert trace.is_file()
                assert (trace.parent / "artifacts" / "answer.txt").read_text() == case["case_id"]
            assert state["usage"]["call_count"] == 6
            assert state["usage"]["tokens"]["input"] == 24
            assert state["usage"]["tokens"]["output"] == 9
            assert all(event.node_ref == "h0" for event in usage_events)
            stages.clear()
            await evaluator.evaluate_batch(**kwargs)
            assert len(started) == 3 and state["usage"]["call_count"] == 6
            if concurrency > 1:
                assert stages[-1]["completed_cases"] == 3
    finally:
        reset_task_openjiuwen_home(outer_token)
    assert get_openjiuwen_home() == parent_home


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_parent", [False, True])
async def test_failure_or_cancellation_drains_siblings_before_return(tmp_path, monkeypatch, cancel_parent):
    evaluator = TeamEvaluator(EvaluatorConfig(transient_case_retry_limit=0))
    both_started = asyncio.Event()
    active = set()
    cleaned = set()

    class Runner:
        async def execute(self, *, case, **kwargs):
            case_id = case["case_id"]
            active.add(case_id)
            if len(active) == 2:
                both_started.set()
            try:
                await both_started.wait()
                if case_id == "a" and not cancel_parent:
                    raise EvaluationInfrastructureError("broken verifier")
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                active.remove(case_id)
                cleaned.add(case_id)

    monkeypatch.setattr(evaluator, "_make_case_runner", Runner)
    task = asyncio.create_task(evaluator.evaluate_batch(
        cases=[{"case_id": item, "input": "test", "reference": {"answer": "ok"}} for item in "ab"],
        team_skill_ref_path="", harness_refs_path="",
        output_dir=str(tmp_path), case_concurrency=2,
    ))
    await asyncio.wait_for(both_started.wait(), timeout=5)
    if cancel_parent:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel_parent else EvaluationInfrastructureError):
        await asyncio.wait_for(task, timeout=5)
    assert not active and cleaned == {"a", "b"}
    assert not (tmp_path / "eval_ref.yaml").exists()
    assert not (tmp_path / "summary.json").exists()


def test_parallel_progress_keeps_case_identity_and_completed_count_distinct():
    stage = case_stage_payload(3, 5, "passed", case_id="third", score=1, completed_cases=1)
    assert stage["case_index"] == 3 and stage["completed_cases"] == 1
    assert stage["name"] == "Cases 1/5 completed" and stage["status"] == "running"
    assert stage["id"] == "evaluate.parallel"
    assert case_stage_payload(1, 5, "passed", completed_cases=5)["status"] == "done"
    assert "completed_cases" not in case_stage_payload(1, 5, "passed")


@pytest.mark.parametrize("value", [0, -1, True])
def test_invalid_concurrency_rejected(value):
    with pytest.raises(ValueError):
        OrchestratorSchedulingConfig.from_dict({"full_evaluation_concurrency": value})


def test_full_evaluation_concurrency_config():
    assert OrchestratorSchedulingConfig.from_dict({}).full_evaluation_concurrency == 2
    assert OrchestratorSchedulingConfig.from_dict({"full_evaluation_concurrency": 1}).full_evaluation_concurrency == 1


def test_native_runners_are_independent_without_repeated_processor_registration():
    evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match"))
    first = evaluator._make_case_runner()
    second = evaluator._make_case_runner()
    assert first.backend is not second.backend
    assert first.judger is not second.judger
    assert first.backend.trajectory_span_processor is second.backend.trajectory_span_processor
    assert first.backend.trajectory_span_processor is evaluator.case_runner.backend.trajectory_span_processor


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_limit", [0, 1])
async def test_partial_resume_and_transient_retry_preserve_finished_cases(tmp_path, monkeypatch, retry_limit):
    evaluator = TeamEvaluator(EvaluatorConfig(evaluation_method="exact_match", transient_case_retry_limit=retry_limit))
    first_persisted = asyncio.Event()
    calls = []
    fail_once = True

    class Backend:
        async def execute(self, *, case, **kwargs):
            nonlocal fail_once
            calls.append(case["case_id"])
            if case["case_id"] == "b" and fail_once:
                await first_persisted.wait()
                fail_once = False
                raise EvaluationInfrastructureError("connection timed out")
            return CaseExecutionResult(response="ok", execution_status="passed")

        async def cleanup(self, *args):
            pass

    async def on_stage(payload):
        if payload["case_id"] == "a" and payload["status"] == "passed":
            first_persisted.set()

    monkeypatch.setattr(evaluator, "_make_case_runner", lambda: CaseRunner(Backend(), ExactMatchJudger()))
    kwargs = {
        "cases": [{"case_id": item, "input": "test", "reference": {"answer": "ok"}} for item in "ab"],
        "team_skill_ref_path": "", "harness_refs_path": "", "output_dir": str(tmp_path / "eval"),
        "case_concurrency": 2, "on_case_stage": on_stage,
    }
    if retry_limit == 0:
        with pytest.raises(EvaluationInfrastructureError):
            await asyncio.wait_for(evaluator.evaluate_batch(**kwargs), timeout=5)
        assert not (tmp_path / "eval" / "eval_ref.yaml").exists()
    result = await asyncio.wait_for(evaluator.evaluate_batch(**kwargs), timeout=5)
    assert calls.count("a") == 1 and calls.count("b") == 2
    evaluation = yaml.safe_load(Path(result).read_text(encoding="utf-8"))
    assert [case["score"] for case in evaluation["cases"]] == [1.0, 1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["failure", "cancel", "cleanup_failure"])
async def test_case_home_restored_on_all_exit_paths(tmp_path, ending):
    original = get_openjiuwen_home()

    class Backend:
        async def execute(self, **kwargs):
            assert get_openjiuwen_home() != original
            if ending == "cancel":
                raise asyncio.CancelledError
            raise EvaluationInfrastructureError("broken execution")

        async def cleanup(self, *args):
            if ending == "cleanup_failure":
                raise RuntimeError("cleanup failed")

    runner = CaseRunner(Backend(), ExactMatchJudger())
    with pytest.raises(asyncio.CancelledError if ending == "cancel" else EvaluationInfrastructureError):
        await runner.execute(case={"case_id": "one", "input": "test"}, output_dir=str(tmp_path / "case"))
    assert get_openjiuwen_home() == original
