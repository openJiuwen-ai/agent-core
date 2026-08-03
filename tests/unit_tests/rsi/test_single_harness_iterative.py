# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the iterative standalone single-harness control plane."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from openjiuwen.rsi.config import (
    AutoCoordinatingHarnessConfig,
    DataLoaderConfig,
    EvaluatorConfig,
    MemberOptimizerConfig,
)
from openjiuwen.rsi.evaluator.runtime_adapters import RSISkillUseRail
from openjiuwen.rsi.single_harness import (
    IterativeSingleHarnessRequest,
    SingleHarnessIterativeOptimizationOrchestrator,
)
from openjiuwen.rsi.single_harness import (
    iterative as iterative_module,
)
from openjiuwen.rsi.single_harness.iterative import (
    _bind_task_acceptance_contracts,
    _candidate_capabilities,
    _failed_machine_evidence,
    _invoked_skill_names,
    _invoked_tool_names,
    _refresh_optimization_experience,
    _tool_names_match,
)


class _Evaluator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.full_checkpoint_promotion_statuses: list[str] = []

    async def evaluate_batch(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        optimized = "candidate_refs" in Path(kwargs["harness_refs_path"]).name
        if optimized and output_dir.name == "full":
            refs = yaml.safe_load(Path(kwargs["harness_refs_path"]).read_text(encoding="utf-8"))
            self.full_checkpoint_promotion_statuses.append(str(refs.get("promotion_status", "")))
        case_refs = []
        for case in kwargs["cases"]:
            case_dir = output_dir / "cases" / str(case["case_id"])
            case_dir.mkdir(parents=True, exist_ok=True)
            result_path = case_dir / "result.json"
            trace_path = case_dir / "trace.json"
            result_path.write_text("{}", encoding="utf-8")
            trace_path.write_text("{}", encoding="utf-8")
            case_refs.append(
                {
                    "case_id": case["case_id"],
                    "status": "passed" if optimized else "failed",
                    "score": 1.0 if optimized else 0.0,
                    "result_path": str(result_path),
                    "trace_path": str(trace_path),
                }
            )
        eval_ref = output_dir / "eval_ref.yaml"
        _write_yaml(
            eval_ref,
            {
                "harness_refs_path": kwargs["harness_refs_path"],
                "team_skill_ref_path": kwargs["team_skill_ref_path"],
                "cases": case_refs,
            },
        )
        return str(eval_ref)


class _Analyzer:
    async def analyze(self, invocation: Any) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_ref = output_dir / "analysis_ref.yaml"
        _write_yaml(analysis_ref, {"issues": []})
        return str(analysis_ref)


class _MemberOptimizer:
    async def optimize(self, **kwargs: Any) -> str:
        output_dir = Path(kwargs["output_dir"])
        run_dir = output_dir / "member_optimization_001"
        run_dir.mkdir(parents=True, exist_ok=True)
        candidate_harness = run_dir / "candidate"
        candidate_harness.mkdir()
        (candidate_harness / "harness.yaml").write_text("name: candidate\n", encoding="utf-8")
        candidate_refs = run_dir / "candidate_refs.yaml"
        _write_yaml(candidate_refs, {"harness_refs": {"solver": str(candidate_harness)}})
        plan_path = run_dir / "plan.yaml"
        _write_yaml(
            plan_path,
            {
                "actions": [
                    {
                        "action_id": "a1",
                        "role": "solver",
                        "action_group": "prompt",
                        "operation": "modify",
                        "target_path": "prompt_sections/debugging.md",
                    }
                ]
            },
        )
        member_ref = run_dir / "member_optimization_ref.yaml"
        _write_yaml(
            member_ref,
            {
                "status": "success",
                "optimized_harness_refs_path": str(candidate_refs),
                "candidate_ready_roles": ["solver"],
                "plan_path": str(plan_path),
            },
        )
        return str(member_ref)


def test_iterative_single_harness_enforces_surfaces_and_promotes(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    config = AutoCoordinatingHarnessConfig(
        evaluator=EvaluatorConfig(backend="single_harness"),
        data_loader=DataLoaderConfig(batch_size=1),
        member_optimizer=MemberOptimizerConfig(
            allowed_action_groups=["prompt", "tool", "skill", "identity"],
            allowed_prompt_surfaces=["identity", "soul", "prompt_section"],
        ),
    )
    evaluator = _Evaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        config,
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )

    assert orchestrator.config.member_optimizer.allowed_action_groups == [
        "prompt",
        "skill",
        "tool",
        "rail",
    ]
    assert orchestrator.config.member_optimizer.action_group_configs == [
        "prompt",
        "skill",
        "tool",
        "rail",
    ]
    assert orchestrator.config.member_optimizer.allowed_prompt_surfaces == ["prompt_section"]
    assert orchestrator.config.member_optimizer.max_roles_per_run == 1
    assert orchestrator.config.member_optimizer.max_actions_per_plan == 1
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["mode"] == "single_harness_benchmark"
    assert report["accepted_candidate_count"] == 1
    assert report["best_score"] == 1.0
    assert "baseline_checkpoint" not in report
    assert "frozen_holdout_case_ids" not in report
    assert evaluator.full_checkpoint_promotion_statuses == ["provisional"]
    evaluation_paths = [str(call["output_dir"]) for call in evaluator.calls]
    assert not any("baseline" in path or "holdout" in path for path in evaluation_paths)
    published_refs_path = Path(result.published_harness_refs_path)
    assert published_refs_path.is_file()
    published_refs = yaml.safe_load(published_refs_path.read_text(encoding="utf-8"))
    assert published_refs["promotion_status"] == "published"
    assert published_refs["role_results"]["solver"]["status"] == "published"
    published_harness = Path(published_refs["harness_refs"]["solver"])
    assert published_harness.is_dir()
    assert published_harness != Path(report["best_harness_refs_path"])
    assert (published_harness / "harness.yaml").read_text(encoding="utf-8") == "name: candidate\n"
    assert report["published_harness_refs_path"] == str(published_refs_path)
    assert all(call["team_skill_ref_path"] == "" for call in evaluator.calls)

    call_count = len(evaluator.calls)
    published_refs_path.unlink()
    shutil.rmtree(published_harness)
    resumed = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
                resume=True,
            )
        )
    )
    assert resumed.report_path == result.report_path
    assert len(evaluator.calls) == call_count
    assert Path(resumed.published_harness_refs_path).is_file()
    repaired_refs = yaml.safe_load(Path(resumed.published_harness_refs_path).read_text(encoding="utf-8"))
    assert Path(repaired_refs["harness_refs"]["solver"]).is_dir()


def test_all_dataset_cases_enter_batches_without_internal_holdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoCandidateOptimizer:
        async def optimize(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            run_dir = output_dir / f"run_{len(list(output_dir.glob('run_*'))) + 1}"
            run_dir.mkdir(parents=True)
            member_ref = run_dir / "member_ref.yaml"
            _write_yaml(
                member_ref,
                {
                    "status": "success",
                    "optimized_harness_refs_path": kwargs["harness_refs_path"],
                    "plan_path": "",
                },
            )
            return str(member_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "case_a", "input": "a"},
                    {"case_id": "case_b", "input": "b"},
                    {"case_id": "case_c", "input": "c"},
                ]
            }
        ),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    evaluator = _Evaluator()
    promotion_candidate_refs: list[str] = []

    def record_promotion(
        member_ref_path: str,
        candidate_refs_path: str,
        gate: dict[str, Any],
    ) -> None:
        del member_ref_path, gate
        promotion_candidate_refs.append(candidate_refs_path)

    monkeypatch.setattr(iterative_module, "_persist_promotion", record_promotion)
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=2),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=1),
        ),
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=NoCandidateOptimizer(),
    )

    asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )

    source_case_ids = {
        str(case["case_id"])
        for call in evaluator.calls
        if Path(call["output_dir"]).name == "source"
        for case in call["cases"]
    }
    assert source_case_ids == {"case_a", "case_b", "case_c"}
    assert orchestrator.config.member_optimizer.candidate_holdout_cases == 0
    assert promotion_candidate_refs
    assert set(promotion_candidate_refs) == {""}


def test_verified_passes_are_protected_from_later_epoch_optimization(
    tmp_path: Path,
) -> None:
    class MixedOutcomeEvaluator:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def evaluate_batch(self, **kwargs: Any) -> str:
            self.calls.append(kwargs)
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            case_refs = []
            for case in kwargs["cases"]:
                case_id = str(case["case_id"])
                passed = case_id == "solved"
                case_refs.append(
                    {
                        "case_id": case_id,
                        "status": "passed" if passed else "failed",
                        "score": 1.0 if passed else 0.0,
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(eval_ref, {"cases": case_refs})
            return str(eval_ref)

    class NoCandidateOptimizer:
        def __init__(self) -> None:
            self.call_count = 0

        async def optimize(self, **kwargs: Any) -> str:
            self.call_count += 1
            run_dir = Path(kwargs["output_dir"]) / f"run_{self.call_count}"
            run_dir.mkdir(parents=True)
            member_ref = run_dir / "member_ref.yaml"
            _write_yaml(
                member_ref,
                {
                    "status": "success",
                    "optimized_harness_refs_path": kwargs["harness_refs_path"],
                    "plan_path": "",
                },
            )
            return str(member_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "solved", "input": "already solved"},
                    {"case_id": "unresolved", "input": "still failing"},
                ]
            }
        ),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    evaluator = MixedOutcomeEvaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            max_epochs=2,
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=2),
        ),
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=NoCandidateOptimizer(),
    )

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )

    source_calls = {
        Path(call["output_dir"]).parts[-3]: [str(case["case_id"]) for case in call["cases"]]
        for call in evaluator.calls
        if Path(call["output_dir"]).name == "source"
    }
    assert source_calls == {
        "e001": ["solved", "unresolved"],
        "e002": ["unresolved"],
    }
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["retained_case_ids"] == ["solved"]


@pytest.mark.parametrize("first_candidate_resolves_all", [False, True])
def test_multiple_batch_issues_follow_latest_source_in_the_same_epoch(
    tmp_path: Path,
    first_candidate_resolves_all: bool,
) -> None:
    class SerialIssueEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            refs_name = Path(kwargs["harness_refs_path"]).stem
            generation = int(refs_name.rsplit("_", 1)[-1]) if refs_name.startswith("candidate_refs_") else 0
            case_refs = []
            for case in kwargs["cases"]:
                case_id = str(case["case_id"])
                passed = generation >= 1 if case_id == "case_001" or first_candidate_resolves_all else generation >= 2
                case_dir = output_dir / "cases" / case_id
                case_dir.mkdir(parents=True, exist_ok=True)
                result_path = case_dir / "result.json"
                trace_path = case_dir / "trace.json"
                result_path.write_text("{}", encoding="utf-8")
                trace_path.write_text("{}", encoding="utf-8")
                case_refs.append(
                    {
                        "case_id": case_id,
                        "status": "passed" if passed else "failed",
                        "score": 1.0 if passed else 0.0,
                        "result_path": str(result_path),
                        "trace_path": str(trace_path),
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(eval_ref, {"cases": case_refs})
            return str(eval_ref)

    class TwoIssueAnalyzer:
        async def analyze(self, invocation: Any) -> str:
            output_dir = Path(invocation.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            analysis_ref = output_dir / "analysis_ref.yaml"
            _write_yaml(
                analysis_ref,
                {
                    "issues": [
                        {
                            "issue_id": "issue_001",
                            "category": "member_harness",
                            "severity": "high",
                            "summary": "First case needs one bounded method.",
                            "recommendation": "Apply the first bounded method.",
                            "affected_cases": ["case_001"],
                            "optimization_target": "member_harness",
                            "metadata": {
                                "attribution": {
                                    "target_ref": "member_harness.solver.skill",
                                }
                            },
                        },
                        {
                            "issue_id": "issue_002",
                            "category": "member_harness",
                            "severity": "high",
                            "summary": "Second case needs a different bounded method.",
                            "recommendation": "Apply the second bounded method.",
                            "affected_cases": ["case_002"],
                            "optimization_target": "member_harness",
                            "metadata": {
                                "attribution": {
                                    "target_ref": "member_harness.solver.skill",
                                }
                            },
                        },
                    ]
                },
            )
            return str(analysis_ref)

    class SerialIssueOptimizer:
        def __init__(self) -> None:
            self.issue_scopes: list[list[str] | None] = []

        async def optimize(self, **kwargs: Any) -> str:
            issue_scope = kwargs.get("optimization_issue_ids")
            self.issue_scopes.append(issue_scope)
            issue_id = str(issue_scope[0])
            generation = len(self.issue_scopes)
            output_dir = Path(kwargs["output_dir"])
            run_dir = output_dir / f"member_optimization_{generation:03d}"
            run_dir.mkdir(parents=True)
            candidate_harness = run_dir / f"candidate_{generation}"
            candidate_harness.mkdir()
            (candidate_harness / "harness.yaml").write_text(
                f"name: candidate_{generation}\n",
                encoding="utf-8",
            )
            candidate_refs = run_dir / f"candidate_refs_{generation}.yaml"
            _write_yaml(
                candidate_refs,
                {
                    "harness_refs": {"solver": str(candidate_harness)},
                },
            )
            plan_path = run_dir / "plan.yaml"
            _write_yaml(
                plan_path,
                {
                    "targets": [
                        {
                            "role": "solver",
                            "attributed_issue_ids": [issue_id],
                        }
                    ],
                    "actions": [
                        {
                            "action_id": f"action_{generation}",
                            "role": "solver",
                            "action_group": "prompt",
                            "operation": "add",
                            "target_path": f"prompt_sections/issue_{generation}.md",
                            "attributed_issue_ids": [issue_id],
                        }
                    ],
                },
            )
            member_ref = run_dir / "member_optimization_ref.yaml"
            _write_yaml(
                member_ref,
                {
                    "status": "success",
                    "optimized_harness_refs_path": str(candidate_refs),
                    "candidate_ready_roles": ["solver"],
                    "plan_path": str(plan_path),
                    "metadata": {
                        "analysis_result_path": kwargs["analysis_result_path"],
                    },
                },
            )
            return str(member_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "case_001", "input": "first"},
                    {"case_id": "case_002", "input": "second"},
                ]
            }
        ),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    optimizer = SerialIssueOptimizer()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            max_epochs=1,
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=2),
        ),
        evaluator=SerialIssueEvaluator(),
        analyzer=TwoIssueAnalyzer(),
        member_optimizer=optimizer,
    )

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )

    expected_scopes = [["issue_001"]] if first_candidate_resolves_all else [["issue_001"], ["issue_002"]]
    assert optimizer.issue_scopes == expected_scopes
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["best_score"] == 1.0
    assert report["accepted_candidate_count"] == len(expected_scopes)
    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    completed = state["completed_batches"]["epoch_001:batch_001"]
    assert [attempt["source_issue_id"] for attempt in completed["candidate_attempts"]] == ["issue_001", "issue_002"]
    expected_accepted_targets = ["case_001"] if first_candidate_resolves_all else ["case_001", "case_002"]
    assert completed["accepted_target_case_ids"] == expected_accepted_targets
    if first_candidate_resolves_all:
        second_attempt = completed["candidate_attempts"][1]
        assert second_attempt["candidate_gate_status"] == "skipped"
        assert second_attempt["candidate_gate_reason"] == ("issue_already_resolved_in_latest_source")
        assert second_attempt["member_optimization_ref_path"] == ""


def test_batch_winner_is_rolled_back_when_clean_full_checkpoint_does_not_improve(
    tmp_path: Path,
) -> None:
    class FullCheckpointRegressionEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            is_candidate = "candidate_refs" in Path(kwargs["harness_refs_path"]).name
            is_candidate_full = is_candidate and output_dir.name == "full"
            passed = is_candidate and not is_candidate_full
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "passed" if passed else "failed",
                            "score": 1.0 if passed else 0.0,
                        }
                    ],
                },
            )
            return str(eval_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "harness.yaml").write_text("name: baseline\n", encoding="utf-8")
    harness_refs = tmp_path / "baseline_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": str(baseline)}})
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=FullCheckpointRegressionEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))

    assert report["accepted_candidate_count"] == 0
    assert report["best_harness_refs_path"] == str(harness_refs.resolve())
    assert report["current_harness_refs_path"] == str(harness_refs.resolve())
    assert report["published_harness_refs_path"] == ""
    assert report["publication_status"] == "not_published_no_improvement"
    assert result.published_harness_refs_path == ""
    assert report["epoch_checkpoints"][0]["status"] == "rejected"
    assert report["candidate_gates"][0]["reason"] == ("candidate_failed_target_replay_checkpoint")


def test_unrelated_full_checkpoint_failure_does_not_remove_target_improvement(
    tmp_path: Path,
) -> None:
    class UnrelatedFullFailureEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            optimized = "candidate_refs" in Path(kwargs["harness_refs_path"]).name
            full_checkpoint = output_dir.name == "full"
            cases = []
            for case in kwargs["cases"]:
                case_id = str(case["case_id"])
                passed = case_id == "unrelated" if not optimized else not (full_checkpoint and case_id == "unrelated")
                cases.append(
                    {
                        "case_id": case_id,
                        "status": "passed" if passed else "failed",
                        "score": 1.0 if passed else 0.0,
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(eval_ref, {"cases": cases})
            return str(eval_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "target", "input": "fix target"},
                    {"case_id": "unrelated", "input": "already solved"},
                ]
            }
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "harness.yaml").write_text("name: baseline\n", encoding="utf-8")
    harness_refs = tmp_path / "baseline_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": str(baseline)}})
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=2),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=UnrelatedFullFailureEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))

    assert report["accepted_candidate_count"] == 1
    assert report["candidate_gates"][0]["status"] == "accepted"
    assert report["epoch_checkpoints"][0]["status"] == "verified"
    assert report["epoch_checkpoints"][0]["failed_target_case_ids"] == []
    assert report["epoch_checkpoints"][0]["failed_retention_case_ids"] == []
    assert report["epoch_checkpoints"][0]["failed_case_ids"] == ["unrelated"]
    assert report["retained_case_ids"] == ["target"]


def test_epoch_selection_scopes_infrastructure_failure_to_candidate_target(
    tmp_path: Path,
) -> None:
    full_eval = tmp_path / "full" / "eval_ref.yaml"
    _write_yaml(
        full_eval,
        {
            "cases": [
                {"case_id": "target", "status": "passed", "score": 1.0},
                {"case_id": "unrelated", "status": "error", "score": 0.0},
            ]
        },
    )
    gate = {
        "target_case_ids": ["target"],
        "capabilities": [
            {
                "action_group": "prompt",
                "operation": "add",
                "runtime_name": "target_prompt",
                "target_case_ids": ["target"],
            }
        ],
    }

    unrelated_error = iterative_module._select_gate_from_epoch_checkpoint(
        gate,
        full_eval_ref=str(full_eval),
        error_case_ids={"unrelated"},
        machine_evidence_case_ids=set(),
    )
    target_error = iterative_module._select_gate_from_epoch_checkpoint(
        gate,
        full_eval_ref=str(full_eval),
        error_case_ids={"target"},
        machine_evidence_case_ids=set(),
    )

    assert unrelated_error["retained"] is True
    assert target_error["retained"] is False
    assert target_error["reason"] == ("candidate_target_inconclusive_at_epoch_checkpoint")


def test_epoch_checkpoint_keeps_effective_skill_and_prunes_failed_skill_once(
    tmp_path: Path,
) -> None:
    class TwoSkillOptimizer:
        def __init__(self) -> None:
            self.calls = 0

        async def optimize(self, **kwargs: Any) -> str:
            self.calls += 1
            source_eval = yaml.safe_load(Path(kwargs["eval_ref_path"]).read_text(encoding="utf-8"))
            source_case_id = str(source_eval["cases"][0]["case_id"])
            skill_name = "keep_skill" if source_case_id == "case_keep" else "drop_skill"
            run_dir = Path(kwargs["output_dir"]) / f"member_{self.calls:03d}"
            candidate = run_dir / "candidate"
            source_refs = yaml.safe_load(Path(kwargs["harness_refs_path"]).read_text(encoding="utf-8"))
            source_harness = Path(source_refs["harness_refs"]["solver"])
            shutil.copytree(source_harness, candidate)
            skill_dir = candidate / "skills" / skill_name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"# {skill_name}\n",
                encoding="utf-8",
            )
            manifest = candidate / "skills" / "skills.yaml"
            manifest_data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            manifest_data["skills"].append(f"skills/{skill_name}")
            _write_yaml(manifest, manifest_data)
            candidate_refs = run_dir / "candidate_refs.yaml"
            _write_yaml(
                candidate_refs,
                {
                    "harness_refs": {"solver": str(candidate)},
                    "promotion_status": "provisional",
                },
            )
            plan_path = run_dir / "plan.yaml"
            _write_yaml(
                plan_path,
                {
                    "actions": [
                        {
                            "action_id": f"skill_{self.calls}",
                            "role": "solver",
                            "action_group": "skill",
                            "operation": "add",
                            "target_path": f"skills/{skill_name}/SKILL.md",
                        }
                    ]
                },
            )
            member_ref = run_dir / "member_ref.yaml"
            _write_yaml(
                member_ref,
                {
                    "status": "success",
                    "optimized_harness_refs_path": str(candidate_refs),
                    "candidate_ready_roles": ["solver"],
                    "plan_path": str(plan_path),
                },
            )
            return str(member_ref)

    class SelectiveReplayEvaluator:
        def __init__(self) -> None:
            self.full_calls = 0

        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            if output_dir.name == "full":
                self.full_calls += 1
            refs = yaml.safe_load(Path(kwargs["harness_refs_path"]).read_text(encoding="utf-8"))
            harness = Path(refs["harness_refs"]["solver"])
            installed = {path.name for path in (harness / "skills").iterdir() if path.is_dir()}
            case_refs = []
            for case in kwargs["cases"]:
                case_id = str(case["case_id"])
                skill_name = "keep_skill" if case_id == "case_keep" else "drop_skill"
                passed = skill_name in installed
                if output_dir.name == "full" and case_id == "case_drop":
                    passed = False
                case_dir = output_dir / "cases" / case_id
                case_dir.mkdir(parents=True, exist_ok=True)
                result_path = case_dir / "result.json"
                triggers = []
                if skill_name in installed:
                    triggers.append(
                        {
                            "mode": "task_start_metadata_trigger",
                            "selected_skill_name": skill_name,
                            "delivered": True,
                        }
                    )
                result_path.write_text(
                    json.dumps(
                        {
                            "metadata": {"execution": {"skill_triggers": triggers}},
                        }
                    ),
                    encoding="utf-8",
                )
                trace_path = case_dir / "trace.json"
                trace_path.write_text("{}", encoding="utf-8")
                case_refs.append(
                    {
                        "case_id": case_id,
                        "status": "passed" if passed else "failed",
                        "score": 1.0 if passed else 0.0,
                        "result_path": str(result_path),
                        "trace_path": str(trace_path),
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(eval_ref, {"cases": case_refs})
            return str(eval_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "case_keep", "input": "keep"},
                    {"case_id": "case_drop", "input": "drop"},
                ]
            }
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline"
    (baseline / "skills" / "baseline").mkdir(parents=True)
    (baseline / "harness.yaml").write_text("name: baseline\n", encoding="utf-8")
    (baseline / "skills" / "baseline" / "SKILL.md").write_text(
        "# baseline\n",
        encoding="utf-8",
    )
    _write_yaml(
        baseline / "skills" / "skills.yaml",
        {"skills": ["skills/baseline"]},
    )
    harness_refs = tmp_path / "baseline_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": str(baseline)}})
    evaluator = SelectiveReplayEvaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            max_epochs=1,
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=TwoSkillOptimizer(),
    )

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )

    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))
    assert evaluator.full_calls == 1
    assert report["accepted_candidate_count"] == 1
    assert report["epoch_checkpoints"][0]["status"] == "filtered"
    assert report["epoch_checkpoints"][0]["post_checkpoint_replay_performed"] is False
    gate_status_by_skill = {
        gate["capabilities"][0]["runtime_name"]: gate["status"] for gate in report["candidate_gates"]
    }
    assert gate_status_by_skill == {
        "keep_skill": "accepted",
        "drop_skill": "rejected",
    }
    published_refs = yaml.safe_load(Path(result.published_harness_refs_path).read_text(encoding="utf-8"))
    published_harness = Path(published_refs["harness_refs"]["solver"])
    published_skills = yaml.safe_load((published_harness / "skills" / "skills.yaml").read_text(encoding="utf-8"))
    assert published_skills["skills"] == ["skills/baseline", "skills/keep_skill"]
    assert (published_harness / "skills" / "keep_skill" / "SKILL.md").is_file()
    assert not (published_harness / "skills" / "drop_skill").exists()
    assert published_refs["checkpoint_filter"]["post_checkpoint_replay_performed"] is False


def test_skill_prompt_uses_runtime_skill_tool() -> None:
    rail = RSISkillUseRail(skills_dir=".", skill_mode=RSISkillUseRail.SKILL_MODE_ALL)
    rail.system_prompt_builder = SimpleNamespace(language="en")
    skill = SimpleNamespace(name="demo", description="Demo skill")
    all_mode = rail._build_skills_section([skill]).render("en")
    rail.skill_mode = RSISkillUseRail.SKILL_MODE_AUTO_LIST
    auto_list = rail._build_skills_section([skill]).render("en")

    assert "call skill_tool" in all_mode
    assert "skill_tool" in auto_list


def test_generated_tool_file_name_matches_runtime_tool_suffix() -> None:
    assert _tool_names_match("shell_platform_guard", "shell_platform_guard_tool")


def test_candidate_capability_uses_skill_directory_as_runtime_name(tmp_path: Path) -> None:
    plan = tmp_path / "plan.yaml"
    _write_yaml(
        plan,
        {
            "actions": [
                {
                    "action_id": "skill_1",
                    "role": "solver",
                    "attributed_issue_ids": ["issue_001"],
                    "action_group": "skill",
                    "operation": "add",
                    "target_path": "skills/post_edit_validation/SKILL.md",
                }
            ],
        },
    )

    capabilities = _candidate_capabilities({"plan_path": str(plan)})

    assert capabilities[0]["runtime_name"] == "post_edit_validation"


def test_candidate_capability_resolves_target_cases_from_analysis(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis_ref.yaml"
    _write_yaml(
        analysis,
        {
            "issues": [
                {
                    "issue_id": "issue_001",
                    "affected_cases": ["case_target"],
                    "evidence": [{"case_id": "case_evidence"}],
                }
            ],
        },
    )
    plan = tmp_path / "plan.yaml"
    _write_yaml(
        plan,
        {
            "targets": [
                {
                    "role": "solver",
                    "attributed_issue_ids": ["issue_001"],
                }
            ],
            "actions": [
                {
                    "action_id": "skill_1",
                    "role": "solver",
                    "action_group": "skill",
                    "operation": "add",
                    "target_path": "skills/post_edit_validation/SKILL.md",
                }
            ],
        },
    )

    capabilities = _candidate_capabilities(
        {
            "plan_path": str(plan),
            "metadata": {"analysis_result_path": str(analysis)},
        }
    )

    assert capabilities[0]["target_case_ids"] == ["case_evidence", "case_target"]


def test_candidate_capability_targets_only_issues_addressed_by_action(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis_ref.yaml"
    _write_yaml(
        analysis,
        {
            "issues": [
                {
                    "issue_id": "issue_semantic",
                    "affected_cases": ["case_semantic"],
                },
                {
                    "issue_id": "issue_path",
                    "affected_cases": ["case_path"],
                },
            ],
        },
    )
    plan = tmp_path / "plan.yaml"
    _write_yaml(
        plan,
        {
            "targets": [
                {
                    "role": "solver",
                    "attributed_issue_ids": ["issue_semantic", "issue_path"],
                }
            ],
            "actions": [
                {
                    "action_id": "skill_1",
                    "role": "solver",
                    "attributed_issue_ids": ["issue_semantic"],
                    "action_group": "skill",
                    "operation": "add",
                    "target_path": "skills/root_owner/SKILL.md",
                }
            ],
        },
    )

    capabilities = _candidate_capabilities(
        {
            "plan_path": str(plan),
            "metadata": {"analysis_result_path": str(analysis)},
        }
    )

    assert capabilities[0]["target_case_ids"] == ["case_semantic"]


def test_candidate_capability_carries_optimizer_only_lever_decision(tmp_path: Path) -> None:
    plan = tmp_path / "plan.yaml"
    _write_yaml(
        plan,
        {
            "actions": [
                {
                    "action_id": "skill_1",
                    "role": "solver",
                    "action_group": "skill",
                    "operation": "add",
                    "target_path": "skills/protocol/SKILL.md",
                    "constraints": {
                        "lever_decision": {
                            "selected_lever": "instruction",
                            "selected_surface": "skill",
                        },
                    },
                }
            ],
        },
    )

    capabilities = _candidate_capabilities({"plan_path": str(plan)})

    assert capabilities[0]["lever_decision"] == {
        "selected_lever": "instruction",
        "selected_surface": "skill",
    }


def test_refresh_optimization_experience_builds_journal_and_scoreboard(
    tmp_path: Path,
) -> None:
    state = {
        "candidate_gates": [
            {
                "epoch": 1,
                "batch_index": 2,
                "status": "accepted",
                "reason": "candidate_passed_epoch_full_checkpoint",
                "target_case_ids": ["case_target"],
                "source_target_score": 0.0,
                "candidate_target_score": 1.0,
                "target_score_delta": 1.0,
                "non_target_score_delta": 0.0,
                "epoch_checkpoint_outcome": {
                    "status": "verified",
                    "eval_ref_path": "epoch/full/eval_ref.yaml",
                    "failed_target_case_ids": [],
                },
                "capabilities": [
                    {
                        "action_id": "skill_1",
                        "action_group": "skill",
                        "lever_decision": {
                            "selected_lever": "instruction",
                            "selected_surface": "skill",
                        },
                    }
                ],
            }
        ],
    }

    _refresh_optimization_experience(state, tmp_path)

    assert state["optimization_journal"][0]["outcome"] == "flipped"
    assert state["lever_scoreboard"]["instruction"] == {
        "attempts": 1,
        "accepted": 1,
        "rejected": 0,
        "target_improvements": 1,
        "partial_contract_progress": 0,
        "regressions": 0,
        "surfaces": {"skill": 1},
        "last_reason": "candidate_passed_epoch_full_checkpoint",
        "average_target_score_delta": 1.0,
    }
    assert Path(state["optimization_journal_path"]).is_file()
    assert Path(state["lever_scoreboard_path"]).is_file()


def test_verifier_delta_preserves_partial_contract_progress(
    tmp_path: Path,
) -> None:
    def write_eval(name: str, *, success: list[str], failure: list[str]) -> Path:
        case_dir = tmp_path / name / "case"
        case_dir.mkdir(parents=True)
        result_path = case_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "evaluation": {
                        "metadata": {
                            "empty_patch": False,
                            "instance_report": {
                                "case_001": {
                                    "patch_successfully_applied": True,
                                    "resolved": False,
                                    "tests_status": {
                                        "FAIL_TO_PASS": {
                                            "success": success,
                                            "failure": failure,
                                        },
                                        "PASS_TO_PASS": {
                                            "success": ["stable"],
                                            "failure": [],
                                        },
                                    },
                                },
                            },
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        eval_ref = tmp_path / name / "eval_ref.yaml"
        _write_yaml(
            eval_ref,
            {
                "cases": [
                    {
                        "case_id": "case_001",
                        "score": 0.0,
                        "status": "failed",
                        "result_path": str(result_path),
                    }
                ],
            },
        )
        return eval_ref

    source = write_eval(
        "source",
        success=[],
        failure=["state_a", "state_b", "state_c"],
    )
    candidate = write_eval(
        "candidate",
        success=["state_a", "state_b"],
        failure=["state_c"],
    )

    delta = iterative_module._verifier_deltas_by_case(
        source,
        candidate,
        {"case_001"},
    )["case_001"]

    assert delta["newly_passed_fail_to_pass"] == ["state_a", "state_b"]
    assert delta["remaining_failed_fail_to_pass"] == ["state_c"]
    assert delta["partial_progress"] is True
    assert (
        iterative_module._classify_gate_failure(
            accepted=False,
            reason="candidate_made_partial_verifier_progress",
            target_case_ids={"case_001"},
            candidate_case_scores={"case_001": 0.0},
            first_edit_steps_by_case={"case_001": 3},
            missing_skill_invocations=[],
            verifier_deltas_by_case={"case_001": delta},
        )
        == "partial_contract_progress"
    )


def test_prior_candidate_feedback_returns_case_scoped_causal_delta() -> None:
    state = {
        "optimization_journal": [
            {
                "experiment_id": "e001-b001-a1",
                "surface": "skill",
                "outcome": "partial_contract_progress",
                "failure_class": "partial_contract_progress",
                "verifier_deltas_by_case": {
                    "case_001": {
                        "newly_passed_fail_to_pass": ["state_a"],
                        "remaining_failed_fail_to_pass": ["state_b"],
                    },
                    "other": {"newly_passed_fail_to_pass": ["x"]},
                },
                "candidate_patch_excerpts_by_case": {
                    "case_001": "diff --git a/module.py b/module.py",
                },
                "candidate_failure_diagnoses": {
                    "case_001": {"root_cause": "state_b was omitted"},
                },
            }
        ],
    }

    feedback = iterative_module._prior_candidate_feedback(
        state,
        [{"case_id": "case_001"}],
    )

    experiment = feedback["by_case"]["case_001"][0]
    assert experiment["verifier_delta"]["remaining_failed_fail_to_pass"] == ["state_b"]
    assert experiment["candidate_failure_diagnosis"]["root_cause"] == ("state_b was omitted")
    assert "other" not in feedback["by_case"]


def test_invoked_skill_names_reads_skill_tool_arguments(tmp_path: Path) -> None:
    trajectory_dir = tmp_path / "tr"
    trajectory_dir.mkdir()
    (trajectory_dir / "solver.jsonl").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "skill_tool",
                            "call_args": json.dumps({"skill_name": "post_edit_validation"}),
                            "call_result": {"success": True},
                        },
                    },
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "skill_tool",
                            "call_args": json.dumps({"skill_name": "skipped_skill"}),
                            "call_result": "[reliability] Tool call skipped before execution",
                        },
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps({"trajectory_dir": str(trajectory_dir)}),
        encoding="utf-8",
    )
    result_path = tmp_path / "result.json"
    result_path.write_text("{}", encoding="utf-8")
    eval_ref = tmp_path / "eval_ref.yaml"
    _write_yaml(
        eval_ref,
        {
            "cases": [
                {
                    "case_id": "case_001",
                    "score": 1.0,
                    "status": "passed",
                    "trace_path": str(trace_path),
                    "result_path": str(result_path),
                }
            ],
        },
    )

    assert _invoked_skill_names(str(eval_ref)) == {"post_edit_validation"}


def test_task_start_trigger_counts_as_natural_skill_delivery(
    tmp_path: Path,
) -> None:
    trajectory_dir = tmp_path / "tr"
    trajectory_dir.mkdir()
    (trajectory_dir / "solver.jsonl").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "bash",
                            "call_args": {"command": "sed -n '1,80p' package.py"},
                            "call_result": {"success": True},
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps({"trajectory_dir": str(trajectory_dir)}),
        encoding="utf-8",
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "execution": {
                        "skill_triggers": [
                            {
                                "mode": "task_start_metadata_trigger",
                                "selected_skill_name": "owner-tracing",
                                "delivered": True,
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    eval_ref = tmp_path / "eval_ref.yaml"
    _write_yaml(
        eval_ref,
        {
            "cases": [
                {
                    "case_id": "case_001",
                    "score": 0.0,
                    "status": "failed",
                    "trace_path": str(trace_path),
                    "result_path": str(result_path),
                }
            ],
        },
    )

    assert _invoked_skill_names(str(eval_ref)) == {"owner-tracing"}


def test_invoked_tool_names_require_successful_execution(tmp_path: Path) -> None:
    trajectory_dir = tmp_path / "tr"
    trajectory_dir.mkdir()
    (trajectory_dir / "solver.jsonl").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "working_validator",
                            "call_result": {"success": True},
                        },
                    },
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "pre_delivery_validator",
                            "call_result": "[reliability] Tool call skipped before execution",
                        },
                    },
                    {
                        "kind": "tool",
                        "error": {"message": "invoke failed"},
                        "detail": {
                            "tool_name": "failed_validator",
                            "call_result": None,
                        },
                    },
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "unavailable_validator",
                            "call_result": None,
                        },
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps({"trajectory_dir": str(trajectory_dir)}),
        encoding="utf-8",
    )
    eval_ref = tmp_path / "eval_ref.yaml"
    _write_yaml(
        eval_ref,
        {
            "cases": [{"case_id": "case_001", "trace_path": str(trace_path)}],
        },
    )

    assert _invoked_tool_names(str(eval_ref)) == {"working_validator"}


def test_candidate_gate_rejects_generated_skill_that_was_not_invoked(tmp_path: Path) -> None:
    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(source_eval, {"cases": [{"case_id": "case_001", "score": 0.0}]})
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    config = AutoCoordinatingHarnessConfig(
        evaluator=EvaluatorConfig(backend="single_harness"),
        member_optimizer=MemberOptimizerConfig(candidate_min_score_delta=0.0),
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        config,
        evaluator=_Evaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[
                {
                    "action_group": "skill",
                    "operation": "add",
                    "runtime_name": "post_edit_validation",
                }
            ],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    assert gate["accepted"] is False
    assert gate["reason"] == "expected_skill_not_invoked_on_target_case"
    assert gate["failure_class"] == "natural_skill_activation_failure"
    assert gate["expected_skill_names"] == ["post_edit_validation"]
    assert gate["invoked_skill_names"] == []
    assert gate["missing_expected_skill_names"] == ["post_edit_validation"]


@pytest.mark.parametrize("action_group", ["skill", "tool"])
def test_candidate_gate_rejects_capability_first_used_after_workspace_edit(
    tmp_path: Path,
    action_group: str,
) -> None:
    class LateCapabilityEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            case_dir = output_dir / "cases" / "case_001"
            trajectory_dir = case_dir / "tr"
            trajectory_dir.mkdir(parents=True, exist_ok=True)
            tool_name = "skill_tool" if action_group == "skill" else "patch_validator"
            call_args = {"skill_name": "patch_validator"} if action_group == "skill" else {"path": "changed.py"}
            (trajectory_dir / "solver.jsonl").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "kind": "tool",
                                "error": None,
                                "detail": {
                                    "tool_name": "bash",
                                    "call_args": json.dumps(
                                        {
                                            "command": (
                                                "python -c \"with open('changed.py', 'w') as f: f.write('patched')\""
                                            ),
                                        }
                                    ),
                                    "call_result": {"success": True},
                                },
                            },
                            {
                                "kind": "tool",
                                "error": None,
                                "detail": {
                                    "tool_name": tool_name,
                                    "call_args": json.dumps(call_args),
                                    "call_result": {"success": True},
                                },
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            trace_path = case_dir / "trace.json"
            trace_path.write_text(
                json.dumps({"trajectory_dir": str(trajectory_dir)}),
                encoding="utf-8",
            )
            result_path = case_dir / "result.json"
            result_path.write_text("{}", encoding="utf-8")
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "passed",
                            "score": 1.0,
                            "result_path": str(result_path),
                            "trace_path": str(trace_path),
                        }
                    ]
                },
            )
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [{"case_id": "case_001", "status": "failed", "score": 0.0}],
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=LateCapabilityEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[
                {
                    "action_group": action_group,
                    "operation": "add",
                    "runtime_name": "patch_validator",
                    "target_case_ids": ["case_001"],
                }
            ],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    capability = "skill" if action_group == "skill" else "tool"
    assert gate["accepted"] is False
    assert gate["reason"] == (f"expected_{capability}_invoked_after_first_persistent_edit")
    assert "patch_validator" in gate[f"invoked_{capability}_names_by_case"]["case_001"]
    assert gate[f"pre_edit_invoked_{capability}_names_by_case"] == {
        "case_001": [] if action_group == "skill" else ["bash"],
    }
    assert gate["first_persistent_edit_step_by_case"] == {"case_001": 0}


def test_candidate_gate_accepts_naturally_used_skill_after_investigation(
    tmp_path: Path,
) -> None:
    class LateHypothesisSkillEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            case_dir = output_dir / "cases" / "case_001"
            trajectory_dir = case_dir / "tr"
            trajectory_dir.mkdir(parents=True, exist_ok=True)
            steps = [
                {
                    "kind": "tool",
                    "error": None,
                    "detail": {
                        "tool_name": "bash",
                        "call_args": json.dumps({"command": "sed -n '1,80p' changed.py"}),
                        "call_result": {"success": True},
                    },
                },
                {
                    "kind": "tool",
                    "error": None,
                    "detail": {
                        "tool_name": "skill_tool",
                        "call_args": json.dumps({"skill_name": "patch_validator"}),
                        "call_result": {"success": True},
                    },
                },
                {
                    "kind": "tool",
                    "error": None,
                    "detail": {
                        "tool_name": "bash",
                        "call_args": json.dumps({"command": "sed -i 's/old/new/' changed.py"}),
                        "call_result": {"success": True},
                    },
                },
            ]
            (trajectory_dir / "solver.jsonl").write_text(
                json.dumps({"steps": steps}) + "\n",
                encoding="utf-8",
            )
            trace_path = case_dir / "trace.json"
            trace_path.write_text(
                json.dumps({"trajectory_dir": str(trajectory_dir)}),
                encoding="utf-8",
            )
            result_path = case_dir / "result.json"
            result_path.write_text("{}", encoding="utf-8")
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "passed",
                            "score": 1.0,
                            "result_path": str(result_path),
                            "trace_path": str(trace_path),
                        }
                    ]
                },
            )
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [{"case_id": "case_001", "status": "failed", "score": 0.0}],
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=LateHypothesisSkillEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[
                {
                    "action_group": "skill",
                    "operation": "add",
                    "runtime_name": "patch_validator",
                    "target_case_ids": ["case_001"],
                }
            ],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    assert gate["accepted"] is True
    assert gate["reason"] == "candidate_improved_target_cases"
    assert gate["failure_class"] == ""
    assert gate["pre_edit_invoked_skill_names_by_case"] == {
        "case_001": ["patch_validator"],
    }


def test_candidate_gate_credits_skill_used_before_workspace_edit(tmp_path: Path) -> None:
    class PreEditSkillEvaluator:
        def __init__(self) -> None:
            self.cases: list[list[dict[str, Any]]] = []

        async def evaluate_batch(self, **kwargs: Any) -> str:
            self.cases.append(kwargs["cases"])
            output_dir = Path(kwargs["output_dir"])
            case_dir = output_dir / "cases" / "case_001"
            trajectory_dir = case_dir / "tr"
            trajectory_dir.mkdir(parents=True, exist_ok=True)
            (trajectory_dir / "solver.jsonl").write_text(
                json.dumps(
                    {
                        "steps": [
                            {
                                "kind": "tool",
                                "error": None,
                                "detail": {
                                    "tool_name": "skill_tool",
                                    "call_args": json.dumps({"skill_name": "patch_validator"}),
                                    "call_result": {"success": True},
                                },
                            },
                            {
                                "kind": "tool",
                                "error": None,
                                "detail": {
                                    "tool_name": "bash",
                                    "call_args": json.dumps(
                                        {
                                            "command": "sed -i 's/old/new/' changed.py",
                                        }
                                    ),
                                    "call_result": {"success": True},
                                },
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            trace_path = case_dir / "trace.json"
            trace_path.write_text(
                json.dumps({"trajectory_dir": str(trajectory_dir)}),
                encoding="utf-8",
            )
            result_path = case_dir / "result.json"
            result_path.write_text("{}", encoding="utf-8")
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "passed",
                            "score": 1.0,
                            "result_path": str(result_path),
                            "trace_path": str(trace_path),
                        }
                    ]
                },
            )
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [{"case_id": "case_001", "status": "failed", "score": 0.0}],
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    evaluator = PreEditSkillEvaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[
                {
                    "action_group": "skill",
                    "operation": "add",
                    "runtime_name": "patch_validator",
                    "target_case_ids": ["case_001"],
                }
            ],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    assert gate["accepted"] is True
    assert gate["reason"] == "candidate_improved_target_cases"
    assert gate["pre_edit_invoked_skill_names_by_case"] == {
        "case_001": ["patch_validator"],
    }
    assert gate["first_persistent_edit_step_by_case"] == {"case_001": 1}
    assert gate["candidate_evaluation_mode"] == "natural"
    assert len(evaluator.cases) == 1
    assert gate["target_confirmation"]["capability_activation_mode"] == "natural"


def test_candidate_gate_requires_each_failing_target_case_to_improve(
    tmp_path: Path,
) -> None:
    class PartiallyImprovedEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {"case_id": "still_failing", "status": "failed", "score": 0.0},
                        {"case_id": "improved", "status": "passed", "score": 1.0},
                    ],
                },
            )
            return str(eval_ref)

    cases = [
        {"case_id": "still_failing", "input": "fix first defect"},
        {"case_id": "improved", "input": "fix second defect"},
    ]
    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [
                {"case_id": "still_failing", "status": "failed", "score": 0.0},
                {"case_id": "improved", "status": "failed", "score": 0.0},
            ],
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=PartiallyImprovedEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=cases,
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 2,
                },
            )(),
        )
    )

    assert gate["accepted"] is False
    assert gate["reason"] == "candidate_did_not_improve_target_cases"
    assert gate["improved_target_case_ids"] == ["improved"]
    assert gate["unimproved_target_case_ids"] == ["still_failing"]


def test_failure_class_distinguishes_no_edit_from_wrong_semantic_edit() -> None:
    from openjiuwen.rsi.single_harness.iterative import (
        _classify_gate_failure,
    )

    common = {
        "accepted": False,
        "reason": "candidate_did_not_improve_target_cases",
        "target_case_ids": {"case_001"},
        "candidate_case_scores": {"case_001": 0.0},
        "missing_skill_invocations": [],
    }

    assert (
        _classify_gate_failure(
            **common,
            first_edit_steps_by_case={},
        )
        == "execution_convergence_failure"
    )
    assert (
        _classify_gate_failure(
            **common,
            first_edit_steps_by_case={"case_001": 12},
        )
        == "semantic_non_reproduction"
    )


def test_task_acceptance_contract_is_bound_only_to_target_case(tmp_path: Path) -> None:
    diagnoses_path = tmp_path / "per_case_diagnoses.json"
    diagnoses_path.write_text(
        json.dumps(
            {
                "per_case_diagnoses": [
                    {
                        "case_id": "target",
                        "root_cause": "The object itself must implement direct next semantics.",
                        "recommendation": "Implement and probe stateful __next__ behavior.",
                        "verifier_observations": {
                            "failed_fail_to_pass_tests": ["TestPersonName::test_next"],
                            "failed_pass_to_pass_tests": [],
                        },
                        "validation_observations": {
                            "contradiction_explanation": "A local suite passed but the official test failed.",
                        },
                        "verifier_failure_output_excerpt": (
                            "FAILED TestPersonName::test_next\n"
                            "E AttributeError: direct next requires prior iterator initialization"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    analysis_ref = tmp_path / "analysis_ref.yaml"
    _write_yaml(
        analysis_ref,
        {
            "metadata": {"per_case_diagnoses_path": str(diagnoses_path)},
        },
    )
    cases = [
        {"case_id": "target", "input": "implement the requested protocol"},
        {"case_id": "unrelated", "input": "leave this task unchanged"},
    ]

    bound, contracts = _bind_task_acceptance_contracts(
        cases,
        analysis_ref=str(analysis_ref),
        target_case_ids={"target"},
    )

    assert set(contracts) == {"target"}
    assert "TestPersonName::test_next" in bound[0]["input"]
    assert "stateful __next__" in bound[0]["input"]
    assert "direct next requires prior iterator initialization" in bound[0]["input"]
    assert "cannot certify equivalence to an unavailable official test" in bound[0]["input"]
    assert "do not copy it into a reusable Skill" in bound[0]["input"]
    assert bound[1] == cases[1]
    assert cases[0]["input"] == "implement the requested protocol"


def test_candidate_gate_keeps_privileged_task_contract_out_of_evaluation_input(
    tmp_path: Path,
) -> None:
    class ContractSolvesWithoutCandidateEvaluator:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            self.inputs.append(str(kwargs["cases"][0]["input"]))
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [{"case_id": "target", "status": "passed", "score": 1.0}],
                },
            )
            return str(eval_ref)

    diagnoses_path = tmp_path / "per_case_diagnoses.json"
    diagnoses_path.write_text(
        json.dumps(
            {
                "per_case_diagnoses": [
                    {
                        "case_id": "target",
                        "root_cause": "Direct next semantics are missing.",
                        "recommendation": "Implement stateful __next__.",
                        "verifier_observations": {
                            "failed_fail_to_pass_tests": ["TestPersonName::test_next"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    analysis_ref = tmp_path / "analysis_ref.yaml"
    _write_yaml(
        analysis_ref,
        {
            "metadata": {"per_case_diagnoses_path": str(diagnoses_path)},
        },
    )
    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [{"case_id": "target", "status": "failed", "score": 0.0}],
        },
    )
    cases = [{"case_id": "target", "input": "implement protocol behavior"}]
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    evaluator = ContractSolvesWithoutCandidateEvaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=cases,
            source_eval_ref=str(source_eval),
            analysis_ref=str(analysis_ref),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    assert evaluator.inputs == ["implement protocol behavior"]
    assert gate["original_source_score"] == 0.0
    assert gate["source_score"] == 0.0
    assert gate["candidate_score"] == 1.0
    assert gate["accepted"] is True
    assert gate["reason"] == "candidate_improved_target_cases"
    assert gate["task_acceptance_contracts"]["target"]["failed_fail_to_pass_tests"] == ["TestPersonName::test_next"]
    assert gate["target_confirmation"]["confirmed"] is True


def test_candidate_gate_uses_the_natural_primary_trial_without_duplicate_confirmation(
    tmp_path: Path,
) -> None:
    class FlakyCandidateEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            confirmed = output_dir.name != "target_confirmation"
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "target",
                            "status": "passed" if confirmed else "failed",
                            "score": 1.0 if confirmed else 0.0,
                        }
                    ],
                },
            )
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [{"case_id": "target", "status": "failed", "score": 0.0}],
        },
    )
    cases = [{"case_id": "target", "input": "fix the behavior"}]
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=FlakyCandidateEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=cases,
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    assert gate["accepted"] is True
    assert gate["reason"] == "candidate_improved_target_cases"
    assert gate["target_confirmation"] == {
        "status": "not_needed",
        "reason": "primary_candidate_evaluation_is_natural",
        "case_count": 1,
        "confirmed": True,
        "capability_activation_mode": "natural",
    }


def test_officially_passed_patch_is_not_overridden_by_completion_text(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps({"result": {"result_type": "error"}}),
        encoding="utf-8",
    )
    eval_ref = tmp_path / "eval_ref.yaml"
    _write_yaml(
        eval_ref,
        {
            "cases": [
                {
                    "case_id": "target",
                    "status": "passed",
                    "score": 1.0,
                    "result_path": str(result_path),
                }
            ],
        },
    )

    assert _failed_machine_evidence(str(eval_ref)) == []


def test_candidate_gate_rejects_inconclusive_source_without_evaluating_candidate(
    tmp_path: Path,
) -> None:
    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [{"case_id": "case_001", "status": "error", "score": 0.0}],
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    evaluator = _Evaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
        ),
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    assert gate["accepted"] is False
    assert gate["reason"] == "source_gate_inconclusive_due_to_error_cases"
    assert evaluator.calls == []


def test_candidate_gate_evaluates_only_the_attributed_target(tmp_path: Path) -> None:
    class TargetOnlyEvaluator:
        def __init__(self) -> None:
            self.case_ids: list[list[str]] = []

        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            self.case_ids.append([str(case["case_id"]) for case in kwargs["cases"]])
            case_refs = []
            for case in kwargs["cases"]:
                case_id = str(case["case_id"])
                case_refs.append(
                    {
                        "case_id": case_id,
                        "status": "passed",
                        "score": 1.0,
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(eval_ref, {"cases": case_refs})
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [
                {"case_id": "case_target", "status": "failed", "score": 0.0},
                {"case_id": "case_unrelated", "status": "passed", "score": 1.0},
            ]
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    cases = [
        {"case_id": "case_target", "input": "fix target"},
        {"case_id": "case_unrelated", "input": "unrelated"},
    ]
    dataset_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    evaluator = TargetOnlyEvaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(
                candidate_min_target_behavior_delta=0.0,
                candidate_holdout_cases=0,
            ),
        ),
        evaluator=evaluator,
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )
    dataset = type(
        "Dataset",
        (),
        {
            "dataset_id": "test",
            "dataset_dir": str(dataset_path.parent),
            "dataset_files": [str(dataset_path)],
            "cases": 2,
        },
    )()

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=cases,
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[
                {
                    "action_group": "prompt",
                    "operation": "add",
                    "runtime_name": "target_prompt",
                    "target_case_ids": ["case_target"],
                }
            ],
            output_dir=tmp_path / "candidate",
            dataset=dataset,
        )
    )

    assert gate["accepted"] is True
    assert gate["reason"] == "candidate_improved_target_cases"
    assert gate["target_score_delta"] == 1.0
    assert evaluator.case_ids == [["case_target"]]
    assert gate["regressed_non_target_case_ids"] == []
    assert gate["non_target_confirmation"] == {
        "status": "not_evaluated",
        "reason": "candidate_gate_is_target_local",
        "case_count": 1,
    }


@pytest.mark.parametrize(
    ("action_group", "reason"),
    [
        ("skill", "expected_skill_not_invoked_on_target_case"),
        ("tool", "expected_tool_not_invoked_on_target_case"),
    ],
)
def test_candidate_gate_does_not_evaluate_unrelated_case_for_attribution(
    tmp_path: Path,
    action_group: str,
    reason: str,
) -> None:
    class WrongCaseSkillEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            case_refs = []
            for case in kwargs["cases"]:
                case_id = str(case["case_id"])
                case_dir = output_dir / "cases" / case_id
                trajectory_dir = case_dir / "tr"
                trajectory_dir.mkdir(parents=True, exist_ok=True)
                if case_id == "case_unrelated":
                    tool_name = "skill_tool" if action_group == "skill" else "post_edit_validation"
                    call_args = (
                        {"skill_name": "post_edit_validation"} if action_group == "skill" else {"path": "changed.py"}
                    )
                    (trajectory_dir / "solver.jsonl").write_text(
                        json.dumps(
                            {
                                "steps": [
                                    {
                                        "kind": "tool",
                                        "error": None,
                                        "detail": {
                                            "tool_name": tool_name,
                                            "call_args": json.dumps(call_args),
                                            "call_result": {"success": True},
                                        },
                                    }
                                ],
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                trace_path = case_dir / "trace.json"
                trace_path.write_text(
                    json.dumps({"trajectory_dir": str(trajectory_dir)}),
                    encoding="utf-8",
                )
                result_path = case_dir / "result.json"
                result_path.write_text("{}", encoding="utf-8")
                case_refs.append(
                    {
                        "case_id": case_id,
                        "status": "passed",
                        "score": 1.0,
                        "result_path": str(result_path),
                        "trace_path": str(trace_path),
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(eval_ref, {"cases": case_refs})
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [
                {"case_id": "case_unrelated", "status": "passed", "score": 1.0},
                {"case_id": "case_target", "status": "failed", "score": 0.0},
            ]
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    config = AutoCoordinatingHarnessConfig(
        evaluator=EvaluatorConfig(backend="single_harness"),
        member_optimizer=MemberOptimizerConfig(candidate_min_score_delta=0.0),
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        config,
        evaluator=WrongCaseSkillEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[
                {"case_id": "case_unrelated", "input": "already passes"},
                {"case_id": "case_target", "input": "fix this"},
            ],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[
                {
                    "action_group": action_group,
                    "operation": "add",
                    "runtime_name": "post_edit_validation",
                    "target_case_ids": ["case_target"],
                }
            ],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 2,
                },
            )(),
        )
    )

    assert gate["accepted"] is False
    assert gate["reason"] == reason
    capability_prefix = "skill" if action_group == "skill" else "tool"
    assert gate[f"invoked_{capability_prefix}_names"] == []
    assert gate[f"invoked_{capability_prefix}_names_by_case"] == {
        "case_target": [],
    }
    assert gate["non_target_case_ids"] == ["case_unrelated"]
    assert gate[f"missing_expected_{capability_prefix}_invocations"] == [
        {
            "runtime_name": "post_edit_validation",
            "target_case_id": "case_target",
        }
    ]


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
