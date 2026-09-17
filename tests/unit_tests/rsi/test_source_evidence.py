# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Source reuse through the real epoch controller and persisted evidence/event contracts."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from openjiuwen.rsi.events import EventNode, EventProgress, EventUsage, NodeStageEvent
from openjiuwen.rsi.harness_rsi.artifact_io import _io_path
from openjiuwen.rsi.harness_rsi.config import AutoCoordinatingHarnessConfig, DataLoaderConfig, EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator import TeamEvaluator
from openjiuwen.rsi.harness_rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import ScriptBasedJudger
from openjiuwen.rsi.harness_rsi.schema import DatasetArtifact
from openjiuwen.rsi.harness_rsi.single_harness import IterativeSingleHarnessRequest
from openjiuwen.rsi.harness_rsi.single_harness.iterative import SingleHarnessIterativeOptimizationOrchestrator
from openjiuwen.rsi.harness_rsi.single_harness.source_evidence import _harness_identity, matching_cases, read_mapping
from openjiuwen.rsi.usage import record_model_usage
from tests.unit_tests.rsi.test_evaluator import _Backend
from tests.unit_tests.rsi.test_single_harness_iterative import _Analyzer, _Evaluator, _MemberOptimizer, _write_yaml


class Evaluator(_Evaluator):
    async def evaluate_batch(self, **kwargs):
        ref = await super().evaluate_batch(**kwargs)
        payload = read_mapping(ref)
        # Keep later checkpoints non-perfect, and distinguish them from H0.
        if Path(kwargs["output_dir"]).name == "full":
            score = sum(Path(call["output_dir"]).name == "full" for call in self.calls) / 10
            for case in payload["cases"]:
                case.update(score=score, status="failed")
        for case in payload["cases"]:
            directory = Path(case["result_path"]).parent
            (directory / "result.json").write_text(
                json.dumps(
                    {
                        "case_id": case["case_id"],
                        "score": case["score"],
                        "status": case["status"],
                        "evaluation": {"passed": case["status"] == "passed", "method": "script-based"},
                    }
                ),
                encoding="utf-8",
            )
            (directory / "trace.json").write_text(
                json.dumps({"case_id": case["case_id"], "steps": []}), encoding="utf-8"
            )
            (directory / "evidence.txt").write_text(case["case_id"], encoding="utf-8")
        _write_yaml(Path(ref), payload)
        await record_model_usage(
            model="fixture", call_id=f"evaluation-{len(self.calls)}", usage={"input_tokens": 10, "output_tokens": 2}
        )
        return ref


class NoIssueAnalyzer:
    def __init__(self):
        self.inputs = []

    async def analyze(self, invocation):
        self.inputs.append(invocation)
        payload = read_mapping(invocation.eval_ref_path)
        # Exercise Analyzer's directory contract, not only the eval_ref cases list.
        results = list(Path(invocation.case_results_dir).glob("*/result.json"))
        assert {read_mapping(path)["case_id"] for path in results} == {case["case_id"] for case in payload["cases"]}
        for path in results:
            assert (path.parent / "evidence.txt").read_text() == read_mapping(path)["case_id"]
        output = Path(invocation.output_dir) / "analysis_ref.yaml"
        _write_yaml(output, {"issues": []})
        return str(output)


@pytest.fixture
def setup(tmp_path):
    harness = tmp_path / "h0"
    harness.mkdir()
    (harness / "harness.yaml").write_text("name: baseline\n")
    refs = tmp_path / "refs.yaml"
    _write_yaml(refs, {"harness_refs": {"solver": str(harness)}})
    cases = [{"case_id": "a", "input": "fix a"}, {"case_id": "b", "input": "fix b"}]
    file = tmp_path / "cases.json"
    file.write_text(json.dumps({"cases": cases}))
    evaluator, analyzer = Evaluator(), NoIssueAnalyzer()
    controller = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            max_epochs=3,
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
        ),
        evaluator=evaluator,
        analyzer=analyzer,
        member_optimizer=_MemberOptimizer(),
    )
    return controller, refs, cases, file


def test_judge_policy_change_invalidates_epoch_source_signature(setup, monkeypatch):
    from openjiuwen.rsi.harness_rsi.evaluator.judger import judge_evidence

    controller, refs, cases, _file = setup
    controller.config = replace(
        controller.config, evaluator=replace(controller.config.evaluator, evaluation_method="llm_as_judge")
    )
    monkeypatch.setattr(judge_evidence, "judge_protocol_identity", lambda: {"policy": "old"})
    old = controller._evaluation_context(cases, str(refs))
    monkeypatch.setattr(judge_evidence, "judge_protocol_identity", lambda: {"policy": "new"})
    new = controller._evaluation_context(cases, str(refs))
    assert old["signature"] != new["signature"]
    assert old["cases"] == new["cases"]


def test_epochs_reuse_h0_then_latest_checkpoint_without_fake_usage(setup, tmp_path):
    controller, refs, _cases, file = setup
    events = []

    async def sink(event):
        events.append(event)

    request = IterativeSingleHarnessRequest(
        dataset_files=[str(file)],
        harness_refs_path=str(refs),
        output_dir=str(tmp_path / "run"),
        auto_full_baseline=True,
    )
    result = asyncio.run(controller.run(request, on_event=sink))
    assert [Path(call["output_dir"]).name for call in controller.evaluator.calls] == [
        "frozen_baseline",
        "full",
        "full",
        "full",
    ]
    assert len(controller.analyzer.inputs) == 6
    for index, invocation in enumerate(controller.analyzer.inputs):
        payload = read_mapping(invocation.eval_ref_path)
        assert [case["score"] for case in payload["cases"]] == [index // 2 / 10]
        assert read_mapping(payload["summary_path"])["average_score"] == index // 2 / 10
        assert len(payload["source_evidence"]["reused_case_ids"]) == 1
        assert not payload["source_evidence"]["evaluated_case_ids"]
        origin = Path(payload["source_evidence"]["evaluations"][0]["eval_ref_path"])
        assert origin.parent.name == ("frozen_baseline" if index < 2 else "full")
        if index >= 2:
            assert origin.parent.parent.name == f"e{index // 2:03d}"
    reuses = [event for event in events if isinstance(event, NodeStageEvent) and event.stage["id"] == "source.reuse"]
    assert len(reuses) == 6
    assert all(event.stage["reused_case_count"] == 1 for event in reuses)
    assert {event.node.node_id for event in events if isinstance(event, EventNode)} == {
        "h0",
        "epoch-001",
        "epoch-002",
        "epoch-003",
    }
    assert len([event for event in events if isinstance(event, EventUsage)]) == 4
    state = read_mapping(result.state_path)
    assert state["usage"]["call_count"] == 4
    assert state["usage"]["tokens"]["input"] == 40
    assert [event.iteration for event in events if isinstance(event, EventProgress)][-1] == 3
    final = [event.node for event in events if isinstance(event, EventNode)][-1]
    assert len(final.extra["source_evidence"]) == 2
    count = len(events)
    asyncio.run(controller.run(replace(request, resume=True), on_event=sink))
    assert len(events) == count


def test_new_harness_from_previous_batch_requires_new_source_and_candidate_retest(setup, tmp_path):
    controller, refs, _cases, file = setup
    controller.analyzer = _Analyzer()
    result = asyncio.run(
        controller.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(file)],
                harness_refs_path=str(refs),
                output_dir=str(tmp_path / "run"),
                auto_full_baseline=True,
                max_iteration=1,
            )
        )
    )
    state = read_mapping(result.state_path)
    first, second = state["completed_batches"].values()
    assert first["source_evidence"]["reused_case_ids"]
    assert first["before_harness_refs_path"] != first["after_harness_refs_path"]
    assert second["before_harness_refs_path"] == first["after_harness_refs_path"]
    assert not second["source_evidence"]
    calls = controller.evaluator.calls
    assert any(Path(call["output_dir"]).name == "source" and call["harness_refs_path"] != str(refs) for call in calls)
    assert len(calls) >= 4  # H0, candidate replay(s), changed-Harness source and full checkpoint.
    assert Path(calls[-1]["output_dir"]).name == "full"


@pytest.mark.parametrize("resume", [False, True])
def test_replay_regression_reenters_analysis_without_changing_promotion(setup, tmp_path, resume):
    controller, refs, cases, file = setup
    cases.append({"case_id": "c", "input": "keep c correct"})
    file.write_text(json.dumps({"cases": cases}))

    class ReplayEvaluator(Evaluator):
        async def evaluate_batch(self, **kwargs):
            ref = await super().evaluate_batch(**kwargs)
            payload = read_mapping(ref)
            directory = Path(kwargs["output_dir"])
            for case in payload["cases"]:
                passed = case["case_id"] == "c" or (
                    case["case_id"] == "b" and directory.parent.name != "e001"
                )
                case.update(score=float(passed), status="passed" if passed else "failed")
                result = read_mapping(case["result_path"])
                result.update(score=case["score"], status=case["status"])
                result["evaluation"]["passed"] = passed
                Path(case["result_path"]).write_text(json.dumps(result))
            _write_yaml(Path(ref), payload)
            return ref

    controller.evaluator = ReplayEvaluator()
    request = IterativeSingleHarnessRequest(
        dataset_files=[str(file)], harness_refs_path=str(refs),
        output_dir=str(tmp_path / "run"), auto_full_baseline=True,
    )
    events = []

    async def sink(event):
        events.append(event)
        if resume and isinstance(event, EventNode) and event.node.node_id == "epoch-001":
            if event.node.type != "RUNNING":
                raise asyncio.CancelledError

    if resume:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(controller.run(request, on_event=sink))
        controller.analyzer = NoIssueAnalyzer()
        result = asyncio.run(controller.run(replace(request, resume=True), on_event=sink))
    else:
        result = asyncio.run(controller.run(request, on_event=sink))

    state = read_mapping(result.state_path)
    batches = state["completed_batches"]
    assert batches["epoch_001:batch_002"]["candidate_gate_reason"] == "no_active_cases"
    regression = batches["epoch_002:batch_002"]
    assert regression["analysis_ref_path"]
    assert regression["source_evidence"]["reused_case_ids"] == ["b"]
    assert regression["source_evidence"]["evaluated_case_ids"] == []
    assert Path(regression["source_evidence"]["evaluations"][0]["eval_ref_path"]).parent.parent.name == "e001"
    assert batches["epoch_003:batch_002"]["candidate_gate_reason"] == "no_active_cases"
    assert all(batches[f"epoch_{epoch:03d}:batch_003"]["candidate_gate_reason"] == "no_active_cases"
               for epoch in range(1, 4))
    assert [Path(call["output_dir"]).name for call in controller.evaluator.calls] == [
        "frozen_baseline", "full", "full", "full",
    ]
    assert state["baseline_score"] == state["best_score"] == 2 / 3
    assert state["current_harness_refs_path"] == str(refs)
    assert state["retained_case_ids"] == ["b", "c"]
    assert not state["candidate_gates"]
    assert all(not item["promotion_applied"] for item in state["epoch_checkpoints"])
    reused = [event for event in events if isinstance(event, NodeStageEvent)
              and event.node_ref == "epoch-002" and event.stage["id"] == "source.reuse"]
    assert len(reused) == 2


@pytest.mark.parametrize("change", ["harness", "judge", "infra"])
def test_historical_pass_cannot_skip_case_without_current_valid_evidence(setup, tmp_path, change):
    controller, refs, cases, file = setup
    _, baseline = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "baseline"))
    payload = read_mapping(baseline)
    for case in payload["cases"]:
        case.update(score=1.0, status="passed")
    _write_yaml(Path(baseline), payload)
    assert controller._source_passing_case_ids(cases, str(refs), [baseline]) == {"a", "b"}
    if change == "harness":
        (tmp_path / "h0" / "new_skill.md").write_text("Changed behavior")
    elif change == "judge":
        controller.config = replace(
            controller.config, evaluator=replace(controller.config.evaluator, evaluation_method="exact-match")
        )
    else:
        _, latest = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "full"))
        payload = read_mapping(latest)
        for case in payload["cases"]:
            case.update(score=None, status="skipped", metadata={"infrastructure_skip": True})
        _write_yaml(Path(latest), payload)
        assert not controller._source_passing_case_ids(cases, str(refs), [baseline, latest])
        return
    assert not controller._source_passing_case_ids(cases, str(refs), [baseline])


async def _baseline(controller, refs, cases, file, directory):
    dataset = DatasetArtifact(dataset_id="test", dataset_dir=str(file.parent), dataset_files=[str(file)])
    baseline = await controller._evaluate(
        cases=cases, harness_refs_path=str(refs), output_dir=directory, dataset=dataset
    )
    return dataset, baseline


def test_ungraded_h0_does_not_emit_score_or_enter_optimization(setup, tmp_path):
    controller, refs, _cases, file = setup
    evaluator = TeamEvaluator(EvaluatorConfig())
    evaluator.case_runner = CaseRunner(backend=_Backend("done"), judger=ScriptBasedJudger())
    controller.evaluator = evaluator
    events = []

    async def sink(event):
        events.append(event)

    directory = tmp_path / "run"
    with pytest.raises(EvaluationInfrastructureError, match="no backend JudgeResult"):
        asyncio.run(
            controller.run(
                IterativeSingleHarnessRequest(
                    dataset_files=[str(file)],
                    harness_refs_path=str(refs),
                    output_dir=str(directory),
                    auto_full_baseline=True,
                ),
                on_event=sink,
            )
        )
    state = read_mapping(directory / "single_harness_state.yaml")
    assert state["baseline_score"] is None
    assert state["status"] != "completed"
    assert not controller.analyzer.inputs
    assert all(event.node.score is None for event in events if isinstance(event, EventNode))
    assert all(event.baseline is None for event in events if isinstance(event, EventProgress))


@pytest.mark.parametrize(
    "method,metadata",
    [
        ("none", {}),
        ("script_based", {"rule_engine_status": "backend_completed"}),
    ],
)
def test_legacy_completion_grade_is_not_source_evidence(setup, tmp_path, method, metadata):
    controller, refs, cases, file = setup
    _dataset, baseline = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "baseline"))
    payload = read_mapping(baseline)
    for case in payload["cases"]:
        path = Path(case["result_path"])
        result = read_mapping(path)
        result["evaluation"].update(method=method, metadata=metadata)
        path.write_text(json.dumps(result), encoding="utf-8")
    assert not matching_cases([baseline], payload["evaluation_context"])


@pytest.mark.parametrize(
    "change", ["harness", "task_model", "judge_model", "trials", "input", "unstamped", "environment"]
)
def test_changed_inputs_or_unknown_provenance_never_reuse(setup, tmp_path, monkeypatch, change):
    controller, refs, cases, file = setup
    judge, model = tmp_path / "judge.yaml", tmp_path / "model.yaml"
    _write_yaml(judge, {"model": "judge-v1"})
    _write_yaml(model, {"model": "task-v1", "judge_config_ref": str(judge)})
    controller.config = replace(
        controller.config, evaluator=replace(controller.config.evaluator, model_config_ref=str(model))
    )
    dataset, baseline = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "baseline"))
    if change == "harness":
        (tmp_path / "h0" / "skill.md").write_text("New behavior")
    elif change == "task_model":
        _write_yaml(model, {"model": "task-v2", "judge_config_ref": str(judge)})
    elif change == "judge_model":
        _write_yaml(judge, {"model": "judge-v2"})
    elif change == "trials":
        for case in cases:
            case["trials"] = 3
    elif change == "input":
        for case in cases:
            case["input"] += " changed"
    elif change == "unstamped":
        payload = read_mapping(baseline)
        payload.pop("evaluation_context")
        _write_yaml(Path(baseline), payload)
    else:
        monkeypatch.setenv("SWEBENCH_ARCH", "changed")
    output = asyncio.run(
        controller._source_evaluation(
            cases=cases,
            harness_refs_path=str(refs),
            output_dir=tmp_path / "source",
            dataset=dataset,
            prior_eval_refs=[baseline],
            batch_index=1,
            node_ref="epoch-001",
            on_event=None,
        )
    )
    assert len(controller.evaluator.calls) == 2
    assert not read_mapping(output).get("source_evidence")


@pytest.mark.parametrize("defect", ["missing_trace", "infra", "corrupt_trace"])
def test_partial_evidence_only_evaluates_missing_cases(setup, tmp_path, defect):
    controller, refs, cases, file = setup
    dataset, baseline = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "baseline"))
    payload = read_mapping(baseline)
    bad = payload["cases"][1]
    if defect == "missing_trace":
        Path(bad["trace_path"]).unlink()
    elif defect == "corrupt_trace":
        Path(bad["trace_path"]).write_text("{unfinished")
    else:
        bad["metadata"] = {"infrastructure_skip": True}
        _write_yaml(Path(baseline), payload)
    output = asyncio.run(
        controller._source_evaluation(
            cases=cases,
            harness_refs_path=str(refs),
            output_dir=tmp_path / "source",
            dataset=dataset,
            prior_eval_refs=[baseline],
            batch_index=1,
            node_ref="epoch-001",
            on_event=None,
        )
    )
    assert [case["case_id"] for case in controller.evaluator.calls[-1]["cases"]] == ["b"]
    view = read_mapping(output)
    assert view["source_evidence"]["reused_case_ids"] == ["a"]
    assert view["source_evidence"]["evaluated_case_ids"] == ["b"]
    assert len(list(Path(view["case_results_dir"]).glob("*/result.json"))) == 2
    assert read_mapping(view["summary_path"])["total_cases"] == 2


def test_promotion_metadata_does_not_invalidate_but_filtered_harness_does(setup, tmp_path):
    controller, refs, cases, file = setup
    _, baseline = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "full"))
    data = read_mapping(refs)
    data.update(promotion_status="promoted", candidate_gate={"status": "accepted"})
    _write_yaml(refs, data)
    assert len(matching_cases([baseline], controller._evaluation_context(cases, str(refs)))) == 2
    new = tmp_path / "filtered"
    new.mkdir()
    (new / "harness.yaml").write_text("name: filtered\n")
    _write_yaml(refs, {"harness_refs": {"solver": str(new)}})
    assert not matching_cases([baseline], controller._evaluation_context(cases, str(refs)))


def test_resume_cannot_charge_old_results_to_changed_model(setup, tmp_path):
    controller, refs, cases, file = setup
    dataset, _ = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "baseline"))
    controller.config = replace(
        controller.config, evaluator=replace(controller.config.evaluator, evaluation_method="exact-match")
    )
    with pytest.raises(ValueError, match="evaluation inputs changed"):
        asyncio.run(
            controller._evaluate(
                cases=cases, harness_refs_path=str(refs), output_dir=tmp_path / "baseline", dataset=dataset
            )
        )
    assert len(controller.evaluator.calls) == 1


@pytest.mark.parametrize("invalid_result", [
    {"status": "error"},
    {"metadata": {"infrastructure_skip": True}},
    {"score": None},
    {"score": "0.5"},
    {"score": float("nan")},
    {"score": float("inf")},
    {"score": float("-inf")},
])
def test_latest_invalid_result_does_not_fall_back_to_older_favorable_result(setup, tmp_path, invalid_result):
    controller, refs, cases, file = setup
    _, baseline = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "baseline"))
    _, latest = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "full"))
    payload = read_mapping(latest)
    payload["cases"][0].update(invalid_result)
    _write_yaml(Path(latest), payload)
    selected = matching_cases([baseline, latest], controller._evaluation_context(cases, str(refs)))
    assert set(selected) == {"b"}
    assert selected["b"][0] == latest


def test_harness_identity_excludes_caches_but_tracks_package_files(tmp_path):
    (tmp_path / "harness.yaml").write_text("name: baseline\n", encoding="utf-8")
    skill = tmp_path / "skills" / "check.md"
    skill.parent.mkdir()
    skill.write_text("Original behavior", encoding="utf-8")
    before = _harness_identity(str(tmp_path))
    for relative in (".git/config", "__pycache__/cached.py", ".pytest_cache/state", "skills/a.pyc", "skills/b.pyo"):
        ignored = tmp_path / relative
        ignored.parent.mkdir(parents=True, exist_ok=True)
        ignored.write_text("ignored", encoding="utf-8")
    assert _harness_identity(str(tmp_path)) == before
    assert list(before["files"]) == ["harness.yaml", "skills/check.md"]
    skill.write_text("Changed behavior", encoding="utf-8")
    assert _harness_identity(str(tmp_path)) != before


def test_completed_run_rejects_resume_after_harness_change(setup, tmp_path):
    controller, refs, _cases, file = setup
    request = IterativeSingleHarnessRequest(
        dataset_files=[str(file)],
        harness_refs_path=str(refs),
        output_dir=str(tmp_path / "run"),
        auto_full_baseline=True,
        max_iteration=1,
    )
    asyncio.run(controller.run(request))
    count = len(controller.evaluator.calls)
    (tmp_path / "h0" / "skill.md").write_text("Changed outside the run")
    with pytest.raises(ValueError, match="initial Harness changed"):
        asyncio.run(controller.run(replace(request, resume=True)))
    assert len(controller.evaluator.calls) == count


def test_source_reuse_preserves_deep_evidence_paths(setup, tmp_path):
    controller, refs, cases, file = setup
    dataset, baseline = asyncio.run(_baseline(controller, refs, cases, file, tmp_path / "baseline"))
    original = read_mapping(baseline)["cases"][0]
    relative = Path("evidence") / ("a" * 90) / ("b" * 90) / ("c" * 90) / "report.json"
    source = Path(original["result_path"]).parent / relative
    assert len(str(source)) > 260
    _io_path(source).parent.mkdir(parents=True, exist_ok=True)
    _io_path(source).write_bytes(b'{"complete": true}')

    output = asyncio.run(
        controller._source_evaluation(
            cases=cases[:1],
            harness_refs_path=str(refs),
            output_dir=tmp_path / "source",
            dataset=dataset,
            prior_eval_refs=[baseline],
            batch_index=1,
            node_ref="epoch-001",
            on_event=None,
        )
    )

    payload = read_mapping(output)
    copied = Path(payload["cases"][0]["result_path"]).parent / relative
    assert _io_path(copied).read_bytes() == _io_path(source).read_bytes()
    assert payload["source_evidence"]["reused_case_ids"] == [cases[0]["case_id"]]
    assert len(controller.evaluator.calls) == 1
    assert not payload["cases"][0]["result_path"].startswith("\\\\?\\")
