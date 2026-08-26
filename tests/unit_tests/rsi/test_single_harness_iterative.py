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
from openjiuwen.rsi.improver_evolution.policy import (
    VersionedImproverPolicy,
    write_improver_policy,
)
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
    _causal_candidate_failure_classification,
    _failed_machine_evidence,
    _initialize_frozen_baseline,
    _invoked_skill_names,
    _invoked_tool_names,
    _must_preserve_budget_for_siblings,
    _refresh_optimization_experience,
    _resume_fingerprint_matches,
    _tool_names_match,
    _validate_and_filter_planned_batches,
)


def test_failed_route_cannot_consume_budget_reserved_for_queued_alternative() -> None:
    assert _must_preserve_budget_for_siblings(
        remaining_attempt_budget=1,
        remaining_sibling_count=1,
    )
    assert not _must_preserve_budget_for_siblings(
        remaining_attempt_budget=2,
        remaining_sibling_count=1,
    )
    assert not _must_preserve_budget_for_siblings(
        remaining_attempt_budget=None,
        remaining_sibling_count=3,
    )


def test_realized_action_without_predicted_outcome_refutes_hypothesis() -> None:
    assert (
        _causal_candidate_failure_classification(
            {
                "case_1": {
                    "state": "triggered",
                    "behavior_activation": {
                        "predicted_behavior_occurred": "yes",
                        "predicted_outcome_occurred": "no",
                    },
                }
            }
        )
        == "action_occurred_but_hypothesis_refuted"
    )


def test_infrastructure_skip_is_not_an_optimization_target(tmp_path: Path) -> None:
    eval_ref = tmp_path / "eval_ref.yaml"
    _write_yaml(
        eval_ref,
        {
            "cases": [
                {"case_id": "valid-failure", "status": "failed", "score": 0.0},
                {
                    "case_id": "grader-timeout",
                    "status": "skipped",
                    "score": None,
                    "metadata": {"infrastructure_skip": True},
                },
            ]
        },
    )

    assert iterative_module._nonpassing_case_ids(str(eval_ref)) == {"valid-failure"}
    assert iterative_module._skipped_case_ids(str(eval_ref)) == {"grader-timeout"}


def test_batch_plan_cannot_omit_or_duplicate_frozen_cases() -> None:
    with pytest.raises(ValueError, match="duplicate requested case id"):
        _validate_and_filter_planned_batches(
            [[{"case_id": "case_1"}], [{"case_id": "case_1"}, {"case_id": "neighbor"}]],
            expected_case_ids={"case_1"},
        )

    with pytest.raises(ValueError, match="omitted requested case ids"):
        _validate_and_filter_planned_batches(
            [[{"case_id": "case_1"}, {"case_id": "neighbor"}]],
            expected_case_ids={"case_1", "case_2"},
        )


def test_frozen_baseline_seeds_global_comparison_without_consuming_batches(tmp_path: Path) -> None:
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    cases = []
    for case_id, score in (("case_001", 1.0), ("case_002", 0.0)):
        result_path = tmp_path / case_id / "result.json"
        result_path.parent.mkdir()
        result_path.write_text("{}", encoding="utf-8")
        cases.append(
            {
                "case_id": case_id,
                "status": "passed" if score == 1.0 else "failed",
                "score": score,
                "result_path": str(result_path),
            }
        )
    baseline_eval_ref = tmp_path / "baseline" / "eval_ref.yaml"
    _write_yaml(
        baseline_eval_ref,
        {
            "harness_refs_path": str(harness_refs),
            "cases": cases,
        },
    )
    state = {
        "best_score": None,
        "best_eval_ref_path": "",
        "baseline_score": None,
        "baseline_eval_ref_path": "",
        "retained_case_ids": [],
    }

    _initialize_frozen_baseline(
        state,
        baseline_eval_ref_path=str(baseline_eval_ref),
        source_harness_refs_path=str(harness_refs),
        expected_case_ids={"case_001", "case_002"},
    )

    assert state["baseline_score"] == 0.5
    assert state["best_score"] == 0.5
    assert state["best_eval_ref_path"] == str(baseline_eval_ref.resolve())
    assert state["retained_case_ids"] == ["case_001"]


def test_auto_full_baseline_is_frozen_inside_single_run(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    evaluator = _Evaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
        ),
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
                auto_full_baseline=True,
            )
        )
    )
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))

    assert Path(evaluator.calls[0]["output_dir"]).name == "frozen_baseline"
    assert report["baseline_score"] == 0.0
    assert Path(report["baseline_eval_ref_path"]).name == "eval_ref.yaml"
    assert report["best_score"] == 1.0


def test_no_candidate_cannot_turn_stochastic_replay_into_best_score(tmp_path: Path) -> None:
    class StochasticReplayEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            score = 1.0 if output_dir.name == "full" else 0.0
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
                        "status": "passed" if score == 1.0 else "failed",
                        "score": score,
                        "result_path": str(result_path),
                        "trace_path": str(trace_path),
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "harness_refs_path": kwargs["harness_refs_path"],
                    "cases": case_refs,
                },
            )
            return str(eval_ref)

    class NoIssueAnalyzer:
        async def analyze(self, invocation: Any) -> str:
            output_dir = Path(invocation.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            analysis_ref = output_dir / "analysis_ref.yaml"
            _write_yaml(analysis_ref, {"issues": []})
            return str(analysis_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}), encoding="utf-8")
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
        ),
        evaluator=StochasticReplayEvaluator(),
        analyzer=NoIssueAnalyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
                auto_full_baseline=True,
            )
        )
    )
    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    checkpoint = state["epoch_checkpoints"][0]

    assert checkpoint["score"] == 1.0
    assert checkpoint["promotion_applied"] is False
    assert checkpoint["noop_initial_score_seed"] is False
    assert state["baseline_score"] == 0.0
    assert state["best_score"] == 0.0


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


def test_default_i0_can_resume_pre_policy_chain_state() -> None:
    requested = {
        "optimization_chain_version": 13,
        "dataset_files": ["cases.json"],
        "dataset_sha256": ["abc"],
        "source_harness_refs_path": "harness_refs.yaml",
        "sibling_candidate_count": 1,
        "improver_policy_digest": iterative_module.default_improver_policy().canonical_digest,
    }
    stored = dict(requested)
    stored["optimization_chain_version"] = 12
    stored.pop("improver_policy_digest")

    assert _resume_fingerprint_matches(stored, requested) is True
    assert (
        _resume_fingerprint_matches(
            stored,
            {**requested, "improver_policy_digest": "sha256:different"},
        )
        is False
    )


def test_native_feedback_chain_does_not_resume_pre_p0_state() -> None:
    requested = {
        "optimization_chain_version": 14,
        "dataset_files": ["cases.json"],
        "dataset_sha256": ["abc"],
        "source_harness_refs_path": "harness_refs.yaml",
        "sibling_candidate_count": 1,
        "improver_policy_digest": iterative_module.default_improver_policy().canonical_digest,
    }
    stored = dict(requested)
    stored["optimization_chain_version"] = 13

    assert _resume_fingerprint_matches(stored, requested) is False


def test_evidence_chain_protocol_does_not_resume_v16_state() -> None:
    requested = {
        "optimization_chain_version": 17,
        "dataset_files": ["cases.json"],
        "dataset_sha256": ["abc"],
        "source_harness_refs_path": "harness_refs.yaml",
        "sibling_candidate_count": 2,
        "improver_policy_digest": iterative_module.default_improver_policy().canonical_digest,
    }
    stored = {**requested, "optimization_chain_version": 16}

    assert _resume_fingerprint_matches(stored, requested) is False


class _Analyzer:
    async def analyze(self, invocation: Any) -> str:
        output_dir = Path(invocation.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        analysis_ref = output_dir / "analysis_ref.yaml"
        eval_ref = yaml.safe_load(Path(invocation.eval_ref_path).read_text(encoding="utf-8")) or {}
        case_ids = [
            str(case.get("case_id", "") or "")
            for case in eval_ref.get("cases", [])
            if isinstance(case, dict)
            and str(case.get("case_id", "") or "")
            and case.get("metadata", {}).get("infrastructure_skip") is not True
        ]
        _write_yaml(
            analysis_ref,
            {
                "issues": [
                    {
                        "issue_id": f"issue_{index:03d}",
                        "category": "member_harness",
                        "severity": "medium",
                        "summary": "The observed behavior did not satisfy the task contract.",
                        "optimization_target": "member_harness",
                        "target_members": ["solver"],
                        "affected_cases": [case_id],
                        "evidence": [{"case_id": case_id, "failure_mode": "test_fixture_failure"}],
                        "recommendation": "Apply the evidenced behavior correction and verify the result.",
                        "metadata": {
                            "attribution": {
                                "evidence_status": "confirmed",
                                "target_ref": "member_harness.solver.prompt",
                                "hypothesis_assessment": [
                                    {
                                        "hypothesis_id": f"h_{index:03d}",
                                        "status": "supported",
                                        "verification_status": "verified",
                                    }
                                ],
                                "general_mechanism": "Verify the requested outcome before finishing.",
                            }
                        },
                    }
                    for index, case_id in enumerate(case_ids, start=1)
                ]
            },
        )
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


def test_strict_causal_analysis_without_issue_stops_before_candidate_generation(tmp_path: Path) -> None:
    class StrictNoIssueAnalyzer:
        async def analyze(self, invocation: Any) -> str:
            output_dir = Path(invocation.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            analysis_ref = output_dir / "analysis_ref.yaml"
            _write_yaml(
                analysis_ref,
                {
                    "issues": [],
                    "metadata": {"analyzer_protocol_version": "generic_behavior_causal_v6"},
                },
            )
            return str(analysis_ref)

    class MustNotRunOptimizer:
        def __init__(self) -> None:
            self.calls = 0

        async def optimize(self, **kwargs: Any) -> str:
            del kwargs
            self.calls += 1
            raise AssertionError("optimizer must not run without an actionable strict causal issue")

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}), encoding="utf-8")
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    optimizer = MustNotRunOptimizer()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
        ),
        evaluator=_Evaluator(),
        analyzer=StrictNoIssueAnalyzer(),
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
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))
    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    completed = state["completed_batches"]["epoch_001:batch_001"]

    assert optimizer.calls == 0
    assert report["candidate_count"] == 0
    assert completed["candidate_gate_status"] == "not_generated"
    assert completed["candidate_gate_reason"] == "no_actionable_analysis_issues"
    assert completed["last_analysis_issue_count"] == 0
    assert completed["last_optimization_hypothesis_count"] == 0


def test_candidate_generation_error_is_recorded_without_aborting_benchmark(tmp_path: Path) -> None:
    class FailingOptimizer:
        async def optimize(self, **kwargs: Any) -> str:
            del kwargs
            raise ValueError("invalid response for api_key=sk-1234567890abcdef")

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}), encoding="utf-8")
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
        ),
        evaluator=_Evaluator(),
        analyzer=_Analyzer(),
        member_optimizer=FailingOptimizer(),
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
    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    gate = state["candidate_gates"][0]

    assert state["status"] == "completed"
    assert gate["reason"] == "member_optimization_status_generation_error"
    assert gate["candidate_generation_error"]["error_type"] == "ValueError"
    assert "sk-1234567890abcdef" not in gate["candidate_generation_error"]["message"]
    assert "[redacted]" in gate["candidate_generation_error"]["message"]


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
    assert orchestrator.config.member_optimizer.max_actions_per_plan == 3
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


def test_frozen_baseline_keeps_epoch_optimization_batch_sequential(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "case_001", "input": "fix first"},
                    {"case_id": "case_002", "input": "fix second"},
                ]
            }
        ),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    baseline_cases = []
    for case_id in ("case_001", "case_002"):
        result_path = tmp_path / "baseline" / "cases" / case_id / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        baseline_cases.append(
            {
                "case_id": case_id,
                "status": "failed",
                "score": 0.0,
                "result_path": str(result_path),
            }
        )
    baseline_eval_ref = tmp_path / "baseline" / "eval_ref.yaml"
    _write_yaml(
        baseline_eval_ref,
        {
            "harness_refs_path": str(harness_refs),
            "cases": baseline_cases,
        },
    )
    config = AutoCoordinatingHarnessConfig(
        evaluator=EvaluatorConfig(backend="single_harness"),
        data_loader=DataLoaderConfig(batch_size=1),
        member_optimizer=MemberOptimizerConfig(),
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
                baseline_eval_ref_path=str(baseline_eval_ref),
            )
        )
    )

    source_calls = [call for call in evaluator.calls if Path(call["output_dir"]).name == "source"]
    assert [len(call["cases"]) for call in source_calls] == [1, 1]
    assert [Path(call["output_dir"]).parent.name for call in source_calls] == ["b001", "b002"]
    full_calls = [call for call in evaluator.calls if Path(call["output_dir"]).name == "full"]
    assert len(full_calls) == 1
    assert len(full_calls[0]["cases"]) == 2
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["baseline_score"] == 0.0
    assert report["best_score"] == 1.0


def test_candidate_evaluation_uses_short_run_level_path(tmp_path: Path) -> None:
    optimization_dir = tmp_path / "single_harness_optimization"

    candidate_dir = iterative_module._candidate_evaluation_output_dir(
        optimization_output_dir=optimization_dir,
        epoch=1,
        batch_index=2,
        attempt_index=3,
        candidate_index=1,
    )

    assert candidate_dir == tmp_path / "ce" / "e001" / "b002" / "a003" / "c001"
    assert optimization_dir not in candidate_dir.parents


def test_three_siblings_freeze_parent_then_promote_best_realized_candidate(
    tmp_path: Path,
) -> None:
    events: list[dict[str, Any]] = []
    realized_scores = {1: 0.4, 2: 0.9, 3: 0.7}

    class SiblingEvaluator:
        def __init__(self) -> None:
            self.full_checkpoint_inputs: list[dict[str, Any]] = []

        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            refs_path = Path(kwargs["harness_refs_path"])
            refs = yaml.safe_load(refs_path.read_text(encoding="utf-8"))
            candidate_index = int(refs.get("candidate_index", 0) or 0)
            is_candidate_gate = "ce" in output_dir.parts
            if is_candidate_gate:
                kind = "candidate_evaluate"
                score = realized_scores[candidate_index]
            elif output_dir.name == "residual_source":
                kind = "residual_evaluate"
                score = 1.0
            elif output_dir.name == "full":
                kind = "full_evaluate"
                score = 1.0
                self.full_checkpoint_inputs.append(
                    {
                        "candidate_index": candidate_index,
                        "promotion_status": str(refs.get("promotion_status", "")),
                        "harness_refs_path": str(refs_path),
                    }
                )
            else:
                kind = "source_evaluate"
                score = 0.0
            events.append(
                {
                    "kind": kind,
                    "candidate_index": candidate_index,
                    "harness_refs_path": str(refs_path),
                    "case_ids": [str(case["case_id"]) for case in kwargs["cases"]],
                    "output_dir": str(output_dir),
                }
            )

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
                        "status": "passed" if score >= 1.0 else "failed",
                        "score": score,
                        "result_path": str(result_path),
                        "trace_path": str(trace_path),
                    }
                )
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "harness_refs_path": str(refs_path),
                    "team_skill_ref_path": kwargs["team_skill_ref_path"],
                    "cases": case_refs,
                },
            )
            return str(eval_ref)

    class SiblingAnalyzer:
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
                            "summary": "The solver does not update the workbook.",
                            "failure_mode": "The required edit is omitted.",
                            "recommendation": "Add a bounded edit decision.",
                            "affected_cases": ["case_001"],
                            "optimization_target": "member_harness",
                            "metadata": {
                                "attribution": {
                                    "evidence_status": "confirmed",
                                    "target_ref": "member_harness.solver.prompt_section",
                                    "hypothesis_assessment": [
                                        {
                                            "hypothesis_id": "h_workbook",
                                            "status": "supported",
                                            "verification_status": "verified",
                                        }
                                    ],
                                }
                            },
                        }
                    ]
                },
            )
            return str(analysis_ref)

    class SiblingOptimizer:
        async def optimize(self, **kwargs: Any) -> str:
            generation = kwargs["optimization_experience"]["sibling_generation"]
            candidate_index = int(generation["candidate_index"])
            events.append(
                {
                    "kind": "optimize",
                    "candidate_index": candidate_index,
                    "harness_refs_path": kwargs["harness_refs_path"],
                    "source_eval_ref_path": kwargs["eval_ref_path"],
                    "analysis_ref_path": kwargs["analysis_result_path"],
                    "output_dir": kwargs["output_dir"],
                    "improver_version_id": kwargs["optimization_experience"]
                    .get("improver_policy", {})
                    .get("version_id", ""),
                }
            )
            output_dir = Path(kwargs["output_dir"])
            run_dir = output_dir / "member_optimization_001"
            run_dir.mkdir(parents=True)
            candidate_harness = run_dir / f"candidate_{candidate_index}"
            candidate_harness.mkdir()
            (candidate_harness / "harness.yaml").write_text(
                f"name: candidate_{candidate_index}\n",
                encoding="utf-8",
            )
            candidate_refs = run_dir / f"candidate_{candidate_index}_refs.yaml"
            _write_yaml(
                candidate_refs,
                {
                    "candidate_index": candidate_index,
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
                            "attributed_issue_ids": ["issue_001"],
                        }
                    ],
                    "actions": [
                        {
                            "action_id": f"candidate_{candidate_index}_action",
                            "role": "solver",
                            "action_group": "prompt",
                            "operation": "add",
                            "target_path": (f"prompt_sections/candidate_{candidate_index}.md"),
                            "expected_effect": (f"Apply edit strategy {candidate_index}."),
                            "attributed_issue_ids": ["issue_001"],
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
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    policy = VersionedImproverPolicy(
        version_id="I_test",
        parent_version_id="I0",
        training_ledger_digest="sha256:test-ledger",
        generation_directives={"require_unique_candidate_fingerprint": True},
    )
    policy_path = write_improver_policy(tmp_path / "improver_policy.yaml", policy)
    evaluator = SiblingEvaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            max_epochs=1,
            evaluator=EvaluatorConfig(backend="single_harness"),
            data_loader=DataLoaderConfig(batch_size=1),
            member_optimizer=MemberOptimizerConfig(
                max_repair_rounds_per_batch=1,
                sibling_candidate_count=3,
                improver_policy_ref=str(policy_path),
            ),
        ),
        evaluator=evaluator,
        analyzer=SiblingAnalyzer(),
        member_optimizer=SiblingOptimizer(),
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

    optimize_events = [event for event in events if event["kind"] == "optimize"]
    candidate_eval_events = [event for event in events if event["kind"] == "candidate_evaluate"]
    assert [event["candidate_index"] for event in optimize_events] == [1, 2, 3]
    assert len(candidate_eval_events) == 3
    assert max(events.index(event) for event in optimize_events) < min(
        events.index(event) for event in candidate_eval_events
    )
    assert {event["harness_refs_path"] for event in optimize_events} == {str(harness_refs)}
    assert len({event["source_eval_ref_path"] for event in optimize_events}) == 1
    assert len({event["analysis_ref_path"] for event in optimize_events}) == 1
    assert {event["improver_version_id"] for event in optimize_events} == {"I_test"}
    assert len({event["output_dir"] for event in optimize_events}) == 3
    assert {Path(event["output_dir"]).name for event in optimize_events} == {
        "c001",
        "c002",
        "c003",
    }
    assert {tuple(event["case_ids"]) for event in candidate_eval_events} == {("case_001",)}

    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    assert state["improver_policy"]["version_id"] == "I_test"
    assert state["improver_policy"]["policy_digest"] == policy.canonical_digest
    gates = sorted(state["candidate_gates"], key=lambda gate: gate["candidate_index"])
    assert [gate["candidate_target_score"] for gate in gates] == [0.4, 0.9, 0.7]
    assert {tuple(gate["target_case_ids"]) for gate in gates} == {("case_001",)}
    assert len({gate["source_eval_ref_path"] for gate in gates}) == 1
    assert len({gate["before_harness_refs_path"] for gate in gates}) == 1
    assert [gate["primary_gate_accepted"] for gate in gates] == [True, True, True]
    assert [gate["within_selection_budget"] for gate in gates] == [True, False, False]
    assert {gate["selection_budget_role"] for gate in gates} == {"counterfactual_metric_only"}
    assert [gate["qualified_for_promotion"] for gate in gates] == [True, True, True]
    assert [gate["selected_for_promotion"] for gate in gates] == [False, True, False]
    assert [gate["status"] for gate in gates] == ["superseded", "accepted", "superseded"]
    winner_refs = gates[1]["candidate_harness_refs_path"]
    assert state["current_harness_refs_path"] == winner_refs
    assert state["best_harness_refs_path"] == winner_refs
    assert evaluator.full_checkpoint_inputs == [
        {
            "candidate_index": 2,
            "promotion_status": "provisional",
            "harness_refs_path": winner_refs,
        }
    ]

    completed = state["completed_batches"]["epoch_001:batch_001"]
    assert completed["repair_round_count"] == 1
    assert len(completed["candidate_attempts"]) == 3
    assert len(completed["improvement_cohort_ids"]) == 1
    cohort_id = completed["improvement_cohort_ids"][0]
    expected_candidate_ids = {gate["candidate_id"] for gate in gates}

    def nested_candidate_ids(value: Any) -> set[str]:
        if isinstance(value, dict):
            found = {str(value["candidate_id"]) for key in ("candidate_id",) if key in value and str(value[key])}
            for item in value.values():
                found.update(nested_candidate_ids(item))
            return found
        if isinstance(value, list):
            found: set[str] = set()
            for item in value:
                found.update(nested_candidate_ids(item))
            return found
        return set()

    cohort = state["improvement_instances"][cohort_id]
    assert nested_candidate_ids(cohort) == expected_candidate_ids
    assert cohort["cohort"]["rank_frozen"] is True
    assert cohort["cohort"]["ranking_policy"] == "static_priority_v1"
    assert cohort["cohort"]["improver_version_id"] == "I_test"
    assert cohort["cohort"]["improver_policy_digest"] == policy.canonical_digest
    assert [candidate["proposal_rank"]["predicted_rank"] for candidate in cohort["candidates"]] == [1, 2, 3]
    assert all(
        set(candidate["proposal_rank"]["ranking_features"]) >= {"executable", "coverage", "atomicity", "duplicate"}
        for candidate in cohort["candidates"]
    )
    assert cohort["metrics"]["best_of_k_gain"]["value"] == pytest.approx(0.9)
    assert cohort["metrics"]["top_m_gain"]["value"] == pytest.approx(0.4)
    assert cohort["metrics"]["selection_regret"]["value"] == pytest.approx(0.5)
    assert cohort["metrics"]["selection_regret"]["predicted_top1_candidate_id"] == gates[0]["candidate_id"]
    assert cohort["selection"]["selected_candidate_id"] == gates[1]["candidate_id"]
    assert cohort["selection"]["promotion_policy"] == "best_realized_qualified_candidate"
    assert cohort["selection"]["top_m_role"] == "counterfactual_metric_only"
    ledger_path = Path(state["candidate_feedback_ledger_path"])
    assert ledger_path.is_file()
    ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
    assert nested_candidate_ids(ledger) == expected_candidate_ids


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


@pytest.mark.parametrize(
    ("first_candidate_resolves_all", "candidate_succeeds"),
    [(False, True), (True, True), (False, False)],
)
def test_multiple_batch_issues_follow_latest_source_in_the_same_epoch(
    tmp_path: Path,
    first_candidate_resolves_all: bool,
    candidate_succeeds: bool,
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
                passed = candidate_succeeds and (
                    generation >= 1 if case_id == "case_001" or first_candidate_resolves_all else generation >= 2
                )
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
                                    "evidence_status": "confirmed",
                                    "target_ref": "member_harness.solver.skill",
                                    "hypothesis_assessment": [
                                        {
                                            "hypothesis_id": "h_case_001",
                                            "status": "supported",
                                            "verification_status": "verified",
                                        }
                                    ],
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
                                    "evidence_status": "confirmed",
                                    "target_ref": "member_harness.solver.skill",
                                    "hypothesis_assessment": [
                                        {
                                            "hypothesis_id": "h_case_002",
                                            "status": "supported",
                                            "verification_status": "verified",
                                        }
                                    ],
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
            member_optimizer=MemberOptimizerConfig(max_repair_rounds_per_batch=1),
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

    expected_scopes = (
        [["issue_001"]] if first_candidate_resolves_all and candidate_succeeds else [["issue_001"], ["issue_002"]]
    )
    assert optimizer.issue_scopes == expected_scopes
    report = yaml.safe_load(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["best_score"] == (1.0 if candidate_succeeds else 0.0)
    assert report["accepted_candidate_count"] == (len(expected_scopes) if candidate_succeeds else 0)
    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    completed = state["completed_batches"]["epoch_001:batch_001"]
    assert [attempt["source_issue_id"] for attempt in completed["candidate_attempts"]] == [
        item[0] for item in expected_scopes
    ]
    expected_accepted_targets = (
        (["case_001"] if first_candidate_resolves_all else ["case_001", "case_002"]) if candidate_succeeds else []
    )
    assert completed["accepted_target_case_ids"] == expected_accepted_targets
    assert completed["repair_round_count"] == 1
    expected_analysis_count = len(expected_scopes) if candidate_succeeds else 1
    assert len(completed["analysis_ref_paths"]) == expected_analysis_count
    assert completed["repair_stop_reason"] == (
        "all_batch_cases_completed" if candidate_succeeds else "repair_round_limit_reached"
    )


def test_single_harness_respects_explicit_action_and_repair_limits() -> None:
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(
                max_actions_per_plan=2,
                max_repair_rounds_per_batch=4,
            ),
        ),
        evaluator=_Evaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    assert orchestrator.config.member_optimizer.max_actions_per_plan == 2
    assert orchestrator.config.member_optimizer.max_repair_rounds_per_batch == 4


def test_partial_candidate_is_reanalyzed_before_case_is_retained(tmp_path: Path) -> None:
    class ProgressiveEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            refs_name = Path(kwargs["harness_refs_path"]).stem
            generation = int(refs_name.rsplit("_", 1)[-1]) if refs_name.startswith("candidate_refs_") else 0
            score = {0: 0.0, 1: 0.5}.get(generation, 1.0)
            case_dir = output_dir / "cases" / "case_001"
            case_dir.mkdir(parents=True, exist_ok=True)
            result_path = case_dir / "result.json"
            trace_path = case_dir / "trace.json"
            result_path.write_text("{}", encoding="utf-8")
            trace_path.write_text("{}", encoding="utf-8")
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "passed" if score >= 1.0 else "failed",
                            "score": score,
                            "result_path": str(result_path),
                            "trace_path": str(trace_path),
                        }
                    ]
                },
            )
            return str(eval_ref)

    class ResidualAnalyzer:
        def __init__(self) -> None:
            self.eval_refs: list[str] = []

        async def analyze(self, invocation: Any) -> str:
            self.eval_refs.append(invocation.eval_ref_path)
            output_dir = Path(invocation.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            index = len(self.eval_refs)
            analysis_ref = output_dir / "analysis_ref.yaml"
            _write_yaml(
                analysis_ref,
                {
                    "issues": [
                        {
                            "issue_id": f"issue_{index}",
                            "category": "member_harness",
                            "severity": "high",
                            "summary": f"Residual behavior {index} remains.",
                            "recommendation": f"Apply repair stage {index}.",
                            "affected_cases": ["case_001"],
                            "optimization_target": "member_harness",
                            "metadata": {
                                "attribution": {
                                    "evidence_status": "confirmed",
                                    "target_ref": "member_harness.solver.prompt_section",
                                    "hypothesis_assessment": [
                                        {
                                            "hypothesis_id": f"h_residual_{index}",
                                            "status": "supported",
                                            "verification_status": "verified",
                                        }
                                    ],
                                }
                            },
                        }
                    ]
                },
            )
            return str(analysis_ref)

    class ProgressiveOptimizer:
        def __init__(self) -> None:
            self.analysis_refs: list[str] = []

        async def optimize(self, **kwargs: Any) -> str:
            self.analysis_refs.append(kwargs["analysis_result_path"])
            generation = len(self.analysis_refs)
            issue_id = str(kwargs["optimization_issue_ids"][0])
            run_dir = Path(kwargs["output_dir"]) / f"run_{generation}"
            run_dir.mkdir(parents=True)
            candidate = run_dir / f"candidate_{generation}"
            candidate.mkdir()
            (candidate / "harness.yaml").write_text(
                f"name: candidate_{generation}\n",
                encoding="utf-8",
            )
            candidate_refs = run_dir / f"candidate_refs_{generation}.yaml"
            _write_yaml(candidate_refs, {"harness_refs": {"solver": str(candidate)}})
            plan_path = run_dir / "plan.yaml"
            _write_yaml(
                plan_path,
                {
                    "targets": [{"role": "solver", "attributed_issue_ids": [issue_id]}],
                    "actions": [
                        {
                            "action_id": f"action_{generation}",
                            "role": "solver",
                            "action_group": "prompt",
                            "operation": "add",
                            "target_path": f"prompt_sections/repair_{generation}.md",
                            "attributed_issue_ids": [issue_id],
                        }
                    ],
                },
            )
            member_ref = run_dir / "member_ref.yaml"
            _write_yaml(
                member_ref,
                {
                    "status": "success",
                    "optimized_harness_refs_path": str(candidate_refs),
                    "plan_path": str(plan_path),
                    "metadata": {"analysis_result_path": kwargs["analysis_result_path"]},
                },
            )
            return str(member_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    analyzer = ResidualAnalyzer()
    optimizer = ProgressiveOptimizer()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(max_repair_rounds_per_batch=2),
        ),
        evaluator=ProgressiveEvaluator(),
        analyzer=analyzer,
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

    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    completed = state["completed_batches"]["epoch_001:batch_001"]
    attempts = completed["candidate_attempts"]
    assert len(attempts) == 2
    assert attempts[0]["completed_target_case_ids"] == []
    assert attempts[0]["residual_case_ids"] == ["case_001"]
    assert attempts[1]["completed_target_case_ids"] == ["case_001"]
    assert completed["retained_case_ids_after_batch"] == ["case_001"]
    assert completed["repair_stop_reason"] == "all_batch_cases_completed"
    assert len(set(optimizer.analysis_refs)) == 2
    assert analyzer.eval_refs[1] == attempts[0]["residual_eval_ref_path"]


def test_residual_repair_stops_when_analyzer_repeats_same_issue(tmp_path: Path) -> None:
    class PartialEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            optimized = "candidate_refs" in Path(kwargs["harness_refs_path"]).name
            score = 0.5 if optimized else 0.0
            case_dir = output_dir / "cases" / "case_001"
            case_dir.mkdir(parents=True, exist_ok=True)
            result_path = case_dir / "result.json"
            trace_path = case_dir / "trace.json"
            result_path.write_text("{}", encoding="utf-8")
            trace_path.write_text("{}", encoding="utf-8")
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "failed",
                            "score": score,
                            "result_path": str(result_path),
                            "trace_path": str(trace_path),
                        }
                    ]
                },
            )
            return str(eval_ref)

    class RepeatingAnalyzer:
        def __init__(self) -> None:
            self.call_count = 0

        async def analyze(self, invocation: Any) -> str:
            self.call_count += 1
            output_dir = Path(invocation.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            analysis_ref = output_dir / "analysis_ref.yaml"
            _write_yaml(
                analysis_ref,
                {
                    "issues": [
                        {
                            "issue_id": f"regenerated_{self.call_count}",
                            "category": "member_harness",
                            "severity": "high",
                            "summary": "The same behavior remains unresolved.",
                            "recommendation": "Apply the same repair direction.",
                            "affected_cases": ["case_001"],
                            "optimization_target": "member_harness",
                            "metadata": {
                                "attribution": {
                                    "evidence_status": "confirmed",
                                    "target_ref": "member_harness.solver.prompt_section",
                                    "hypothesis_assessment": [
                                        {
                                            "hypothesis_id": "h_repeated",
                                            "status": "supported",
                                            "verification_status": "verified",
                                        }
                                    ],
                                }
                            },
                        }
                    ]
                },
            )
            return str(analysis_ref)

    class OneCandidateOptimizer(_MemberOptimizer):
        def __init__(self) -> None:
            self.call_count = 0

        async def optimize(self, **kwargs: Any) -> str:
            self.call_count += 1
            member_ref = await super().optimize(**kwargs)
            member_info = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
            plan_path = Path(member_info["plan_path"])
            plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
            issue_id = str(kwargs["optimization_issue_ids"][0])
            plan["targets"] = [{"role": "solver", "attributed_issue_ids": [issue_id]}]
            plan["actions"][0]["attributed_issue_ids"] = [issue_id]
            _write_yaml(plan_path, plan)
            member_info["metadata"] = {"analysis_result_path": kwargs["analysis_result_path"]}
            _write_yaml(Path(member_ref), member_info)
            return member_ref

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    analyzer = RepeatingAnalyzer()
    optimizer = OneCandidateOptimizer()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(max_repair_rounds_per_batch=3),
        ),
        evaluator=PartialEvaluator(),
        analyzer=analyzer,
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

    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    completed = state["completed_batches"]["epoch_001:batch_001"]
    assert optimizer.call_count == 1
    assert analyzer.call_count == 2
    assert completed["repair_stop_reason"] == "repeated_issue_detected"
    assert completed["residual_case_ids"] == ["case_001"]
    assert completed["retained_case_ids_after_batch"] == []
    assert state["retained_case_ids"] == []
    assert state["current_harness_refs_path"] == str(harness_refs)


def test_rejected_candidate_failure_analysis_drives_next_repair_round(tmp_path: Path) -> None:
    class RepairEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            refs_name = Path(kwargs["harness_refs_path"]).name
            score = 1.0 if refs_name == "candidate_refs_2.yaml" else 0.0
            case_dir = output_dir / "cases" / "case_001"
            case_dir.mkdir(parents=True, exist_ok=True)
            result_path = case_dir / "result.json"
            trace_path = case_dir / "trace.json"
            result_path.write_text("{}", encoding="utf-8")
            trace_path.write_text("{}", encoding="utf-8")
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": str(case["case_id"]),
                            "status": "passed" if score else "failed",
                            "score": score,
                            "result_path": str(result_path),
                            "trace_path": str(trace_path),
                        }
                        for case in kwargs["cases"]
                    ]
                },
            )
            return str(eval_ref)

    class RepairAnalyzer:
        def __init__(self) -> None:
            self.call_count = 0

        async def analyze(self, invocation: Any) -> str:
            self.call_count += 1
            output_dir = Path(invocation.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            analysis_ref = output_dir / "analysis_ref.yaml"
            scope = "persisted artifacts" if self.call_count == 1 else "every output channel"
            issues = [
                {
                    "issue_id": "issue_001",
                    "category": "member_harness",
                    "summary": f"Mask secrets in {scope}.",
                    "recommendation": f"Apply credential masking to {scope}.",
                    "affected_cases": ["case_001"],
                    "optimization_target": "member_harness",
                    "metadata": {
                        "attribution": {
                            "evidence_status": "confirmed",
                            "target_ref": "member_harness.solver.prompt_section",
                            "hypothesis_assessment": [
                                {
                                    "hypothesis_id": f"h_scope_{self.call_count}",
                                    "status": "supported",
                                    "verification_status": "verified",
                                }
                            ],
                        }
                    },
                }
            ]
            if self.call_count == 1:
                issues.append(
                    {
                        "issue_id": "issue_002",
                        "category": "member_harness",
                        "summary": "A separate sibling issue affects case two.",
                        "recommendation": "Repair case two after resolving case one's feedback.",
                        "affected_cases": ["case_002"],
                        "optimization_target": "member_harness",
                        "metadata": {
                            "attribution": {
                                "evidence_status": "confirmed",
                                "target_ref": "member_harness.solver.prompt_section",
                                "hypothesis_assessment": [
                                    {
                                        "hypothesis_id": "h_sibling_case_002",
                                        "status": "supported",
                                        "verification_status": "verified",
                                    }
                                ],
                            }
                        },
                    }
                )
            _write_yaml(
                analysis_ref,
                {"issues": issues},
            )
            return str(analysis_ref)

    class RepairOptimizer:
        def __init__(self) -> None:
            self.parent_refs: list[str] = []
            self.analysis_refs: list[str] = []
            self.issue_ids: list[str] = []

        async def optimize(self, **kwargs: Any) -> str:
            self.parent_refs.append(kwargs["harness_refs_path"])
            self.analysis_refs.append(kwargs["analysis_result_path"])
            issue_id = str(kwargs["optimization_issue_ids"][0])
            self.issue_ids.append(issue_id)
            generation = len(self.parent_refs)
            run_dir = Path(kwargs["output_dir"]) / f"repair_{generation}"
            run_dir.mkdir(parents=True)
            candidate = run_dir / f"candidate_{generation}"
            candidate.mkdir()
            (candidate / "harness.yaml").write_text(f"name: repair_{generation}\n", encoding="utf-8")
            candidate_refs = run_dir / f"candidate_refs_{generation}.yaml"
            _write_yaml(candidate_refs, {"harness_refs": {"solver": str(candidate)}})
            plan_path = run_dir / "plan.yaml"
            _write_yaml(
                plan_path,
                {
                    "targets": [{"role": "solver", "attributed_issue_ids": [issue_id]}],
                    "actions": [
                        {
                            "action_id": f"repair_{generation}",
                            "role": "solver",
                            "action_group": "prompt",
                            "operation": "modify",
                            "target_path": "prompt_sections/security.md",
                            "attributed_issue_ids": [issue_id],
                        }
                    ],
                },
            )
            member_ref = run_dir / "member_ref.yaml"
            _write_yaml(
                member_ref,
                {
                    "status": "success",
                    "optimized_harness_refs_path": str(candidate_refs),
                    "plan_path": str(plan_path),
                    "metadata": {"analysis_result_path": kwargs["analysis_result_path"]},
                },
            )
            return str(member_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "case_001", "input": "fix first"},
                    {"case_id": "case_002", "input": "fix second"},
                ]
            }
        ),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    analyzer = RepairAnalyzer()
    optimizer = RepairOptimizer()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(max_repair_rounds_per_batch=2),
        ),
        evaluator=RepairEvaluator(),
        analyzer=analyzer,
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

    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    completed = state["completed_batches"]["epoch_001:batch_001"]
    assert len(completed["candidate_attempts"]) == 2
    assert completed["candidate_attempts"][0]["candidate_gate_status"] == "rejected"
    assert completed["candidate_attempts"][1]["candidate_gate_status"] == "accepted"
    assert completed["repair_stop_reason"] == "all_batch_cases_completed"
    assert analyzer.call_count == 2
    assert optimizer.parent_refs[1].endswith("candidate_refs_1.yaml")
    assert "failure_analysis" in optimizer.analysis_refs[1]
    assert optimizer.issue_ids == ["issue_001", "issue_001"]
    assert state["candidate_gates"][0]["selected_as_repair_parent"] is True


def test_repair_parent_prefers_native_signal_when_pass_hat_k_ties() -> None:
    gates = [
        {
            "candidate_id": "native_low",
            "candidate_target_score": 0.0,
            "target_score_delta": 0.0,
            "candidate_native_target_score": 0.35,
            "native_target_score_delta": 0.15,
            "native_dimension_delta": 0.1,
            "predicted_rank": 1,
            "capabilities": [],
        },
        {
            "candidate_id": "native_high",
            "candidate_target_score": 0.0,
            "target_score_delta": 0.0,
            "candidate_native_target_score": 0.82,
            "native_target_score_delta": 0.62,
            "native_dimension_delta": 0.4,
            "predicted_rank": 2,
            "capabilities": [],
        },
    ]

    repair_parent = iterative_module._best_realized_sibling_gate(gates)
    assert repair_parent is not None
    assert repair_parent["candidate_id"] == "native_high"

    feedback = {
        "candidates": [{"candidate_id": "native_low"}, {"candidate_id": "native_high"}],
        "selection": {},
    }
    iterative_module._attach_native_signal_feedback(feedback, gates)
    assert feedback["candidates"][1]["continuous_outcome"] == {
        "source_native_target_score": None,
        "candidate_native_target_score": 0.82,
        "native_target_score_delta": 0.62,
        "native_dimension_delta": 0.4,
        "source_signal_sources_by_case": {},
        "candidate_signal_sources_by_case": {},
        "role": "sibling_and_repair_ranking_only",
        "promotion_authority": "eval_ref_case_score",
    }
    assert feedback["selection"]["realized_sort_policy"] == "strict_eval_ref_score_then_continuous_signal_v1"


def test_pass_hat_k_stays_ahead_of_native_signal_in_realized_ranking() -> None:
    pass_hat_k_winner = {
        "candidate_id": "pass_hat_k_winner",
        "candidate_target_score": 1.0,
        "target_score_delta": 1.0,
        "candidate_native_target_score": 0.1,
        "native_target_score_delta": -0.4,
        "predicted_rank": 2,
        "capabilities": [],
    }
    native_only_winner = {
        "candidate_id": "native_only_winner",
        "candidate_target_score": 0.0,
        "target_score_delta": 0.0,
        "candidate_native_target_score": 0.99,
        "native_target_score_delta": 0.8,
        "predicted_rank": 1,
        "capabilities": [],
    }

    selected = iterative_module._best_realized_sibling_gate([native_only_winner, pass_hat_k_winner])
    assert selected is pass_hat_k_winner


def test_native_signal_improvement_cannot_pass_candidate_gate(tmp_path: Path) -> None:
    def write_eval(
        root: Path,
        *,
        pass_hat_k_score: float,
        trial_scores: list[float],
        dimension_scores: dict[str, float],
    ) -> str:
        case_dir = root / "cases" / "case_001"
        case_dir.mkdir(parents=True, exist_ok=True)
        result_path = case_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "evaluation": {
                        "metadata": {
                            "optimization_signals": {
                                "schema_version": 1,
                                "continuous_score": {
                                    "availability": "available",
                                    "value": sum(trial_scores) / len(trial_scores),
                                    "source": "test_evaluator",
                                },
                                "dimensions": {
                                    name: {
                                        "availability": "available",
                                        "value": value,
                                        "source": "test_evaluator",
                                    }
                                    for name, value in dimension_scores.items()
                                },
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        trace_path = case_dir / "trace.json"
        trace_path.write_text("{}", encoding="utf-8")
        eval_ref = root / "eval_ref.yaml"
        _write_yaml(
            eval_ref,
            {
                "cases": [
                    {
                        "case_id": "case_001",
                        "status": "passed" if pass_hat_k_score >= 1.0 else "failed",
                        "score": pass_hat_k_score,
                        "result_path": str(result_path),
                        "trace_path": str(trace_path),
                    }
                ]
            },
        )
        return str(eval_ref)

    source_eval = write_eval(
        tmp_path / "source",
        pass_hat_k_score=0.0,
        trial_scores=[0.1, 0.2, 0.3],
        dimension_scores={"accuracy": 0.1, "completeness": 0.3},
    )

    class NativeOnlyImprovementEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            return write_eval(
                Path(kwargs["output_dir"]),
                pass_hat_k_score=0.0,
                trial_scores=[0.8, 0.9, 1.0],
                dimension_scores={"accuracy": 0.8, "completeness": 0.9},
            )

    class FeedbackAnalyzer(_Analyzer):
        def __init__(self) -> None:
            self.paired_feedback: list[dict[str, Any]] = []

        async def analyze(self, invocation: Any) -> str:
            self.paired_feedback.append(dict(invocation.prior_candidate_feedback))
            return await super().analyze(invocation)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    analyzer = FeedbackAnalyzer()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=NativeOnlyImprovementEvaluator(),
        analyzer=analyzer,
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=source_eval,
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[],
            output_dir=tmp_path / "candidate_eval",
            dataset=SimpleNamespace(
                dataset_id="test",
                dataset_dir=str(dataset_path.parent),
                dataset_files=[str(dataset_path)],
                cases=1,
            ),
        )
    )

    assert gate["accepted"] is False
    assert gate["reason"] == "candidate_did_not_improve_target_cases"
    assert gate["candidate_target_score"] == 0.0
    assert gate["source_native_target_score"] == pytest.approx(0.2)
    assert gate["candidate_native_target_score"] == pytest.approx(0.9)
    assert gate["native_target_score_delta"] == pytest.approx(0.7)
    assert gate["native_dimension_delta"] == pytest.approx(0.65)
    assert gate["native_signal_role"] == "sibling_and_repair_ranking_only"
    feedback = analyzer.paired_feedback[0]["by_case"]["case_001"][0]
    assert feedback["source_native_score"] == pytest.approx(0.2)
    assert feedback["candidate_native_score"] == pytest.approx(0.9)
    assert feedback["native_score_delta"] == pytest.approx(0.7)
    assert feedback["native_dimension_deltas"] == {
        "accuracy": pytest.approx(0.7),
        "completeness": pytest.approx(0.6),
    }
    assert feedback["native_signal_role"] == "sibling_and_repair_ranking_only"
    assert feedback["schema_version"] == 2
    assert feedback["observed_outcome"]["strict_score"] == {
        "source": 0.0,
        "candidate": 0.0,
        "delta": 0.0,
    }
    assert feedback["observed_outcome"]["continuous_score"]["source"] == pytest.approx(0.2)
    assert feedback["observed_outcome"]["continuous_score"]["candidate"] == pytest.approx(0.9)
    assert feedback["observed_outcome"]["continuous_score"]["delta"] == pytest.approx(0.7)
    assert feedback["observed_outcome"]["dimension_deltas"] == {
        "accuracy": pytest.approx(0.7),
        "completeness": pytest.approx(0.6),
    }
    assert feedback["activation"]["delivery"]["availability"] == "observed"
    assert feedback["activation"]["delivery"]["state"] == "executed"
    assert feedback["activation"]["availability"] in {"not_applicable", "not_instrumented"}
    checkpoint_selection = iterative_module._select_gate_from_epoch_checkpoint(
        gate,
        full_eval_ref=gate["candidate_eval_ref_path"],
        error_case_ids=set(),
        machine_evidence_case_ids=set(),
    )
    assert checkpoint_selection["retained"] is False
    assert checkpoint_selection["reason"] == "candidate_failed_target_replay_checkpoint"


def test_paired_prompt_activation_separates_delivery_from_behavior_observation() -> None:
    activation = iterative_module._paired_candidate_activation(
        [
            {
                "action_group": "prompt",
                "operation": "modify",
                "target_case_ids": ["case_001"],
            }
        ],
        case_id="case_001",
        pre_edit_tools_by_case={"case_001": set()},
        pre_edit_skills_by_case={"case_001": set()},
    )

    assert activation["delivery"] == {
        "availability": "observed",
        "state": "executed",
        "evidence": "candidate_harness_was_used_for_paired_evaluation",
    }
    assert activation["behavior_activation"] == {
        "availability": "not_instrumented",
        "state": "unknown",
        "reason": "surface_has_no_observable_activation_event",
    }
    assert activation["state"] == "unknown"


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


def test_mixed_opaque_snapshot_checkpoint_is_rejected_atomically() -> None:
    gates = [
        {"candidate_id": "c1", "composition_mode": "opaque_snapshot"},
        {"candidate_id": "c2", "composition_mode": "opaque_snapshot"},
    ]
    selections = [
        {"retained": True, "reason": "target_passed"},
        {"retained": False, "reason": "target_failed"},
    ]

    blocked = iterative_module._reject_mixed_opaque_snapshot_selection(gates, selections)

    assert blocked is True
    assert all(selection["retained"] is False for selection in selections)
    assert {selection["reason"] for selection in selections} == {"epoch_opaque_snapshot_partial_retention_unsupported"}


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


def test_candidate_capability_preserves_pre_evaluation_causal_contract(tmp_path: Path) -> None:
    plan = tmp_path / "plan.yaml"
    _write_yaml(
        plan,
        {
            "actions": [
                {
                    "action_id": "prompt_1",
                    "role": "solver",
                    "attributed_issue_ids": ["issue_001"],
                    "action_group": "prompt",
                    "operation": "modify",
                    "target_path": "system_prompt.md",
                    "description": "Make the existing decision rule operational.",
                    "rationale": "The prior behavior was not observed.",
                    "intervention": "On trigger A, perform B and verify C.",
                    "expected_effect": "B occurs before completion.",
                    "constraints": {
                        "analyzer_counterfactual_predictions": ["B is visible in the trace"],
                    },
                }
            ]
        },
    )

    capability = _candidate_capabilities({"plan_path": str(plan)})[0]

    assert capability["attributed_issue_ids"] == ["issue_001"]
    assert capability["intervention"] == "On trigger A, perform B and verify C."
    assert capability["description"] == "Make the existing decision rule operational."
    assert capability["rationale"] == "The prior behavior was not observed."
    assert capability["analyzer_counterfactual_predictions"] == ["B is visible in the trace"]


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
    assert [item["requirement_id"] for item in delta["newly_passed_requirements"]] == ["state_a", "state_b"]
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


def test_verifier_delta_recognizes_evobench_criteria_progress(
    tmp_path: Path,
) -> None:
    def write_eval(name: str, *, passed_count: int) -> Path:
        case_dir = tmp_path / name / "case"
        case_dir.mkdir(parents=True)
        result_path = case_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "evaluation": {
                        "metadata": {
                            "requirement_results": {
                                "schema_version": 1,
                                "items": [
                                    {
                                        "requirement_id": f"criterion_{index}",
                                        "group": "requirement",
                                        "passed": index <= passed_count,
                                        "score": 1.0 if index <= passed_count else 0.0,
                                        "source": "official_result.judge_detail.criteria",
                                    }
                                    for index in range(1, 10)
                                ],
                            }
                        }
                    }
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
                ]
            },
        )
        return eval_ref

    delta = iterative_module._verifier_deltas_by_case(
        write_eval("source_evobench", passed_count=3),
        write_eval("candidate_evobench", passed_count=8),
        {"case_001"},
    )["case_001"]

    assert [item["requirement_id"] for item in delta["newly_passed_requirements"]] == [
        "criterion_4",
        "criterion_5",
        "criterion_6",
        "criterion_7",
        "criterion_8",
    ]
    assert [item["requirement_id"] for item in delta["remaining_failed_requirements"]] == ["criterion_9"]
    assert delta["regressed_requirements"] == []
    assert delta["partial_progress"] is True


def test_prior_candidate_feedback_returns_case_scoped_causal_delta() -> None:
    state = {
        "optimization_journal": [
            {
                "experiment_id": "e001-b001-a1",
                "surface": "skill",
                "outcome": "partial_contract_progress",
                "status": "rejected",
                "reason": "candidate_made_partial_verifier_progress",
                "failure_class": "partial_contract_progress",
                "predicted_rank": 1,
                "predicted_score": 125.0,
                "source_target_score": 0.0,
                "candidate_target_score": 0.4,
                "target_score_delta": 0.4,
                "selected_for_promotion": False,
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
                    "case_001": {
                        "root_cause": "state_b was omitted",
                        "prior_experiment_assessment": {
                            "availability": "available",
                            "causal_hypothesis_status": "falsified",
                        },
                    },
                },
                "causal_intervention_contracts": [
                    {
                        "action_id": "a1",
                        "target_case_ids": ["case_001"],
                        "source_causal_hypothesis_id": "h_state_b",
                        "predicted_behavior_and_outcome": "state_b becomes valid",
                    }
                ],
            }
        ],
    }

    feedback = iterative_module._prior_candidate_feedback(
        state,
        [{"case_id": "case_001"}],
    )

    experiment = feedback["by_case"]["case_001"][0]
    assert experiment["verifier_delta"]["remaining_failed_fail_to_pass"] == ["state_b"]
    assert experiment["predicted_rank"] == 1
    assert experiment["source_target_score"] == 0.0
    assert experiment["candidate_target_score"] == 0.4
    assert experiment["target_score_delta"] == 0.4
    assert experiment["selected_for_promotion"] is False
    assert experiment["candidate_failure_diagnosis"]["root_cause"] == ("state_b was omitted")
    assert experiment["causal_intervention_contracts"][0]["predicted_behavior_and_outcome"] == ("state_b becomes valid")
    assert experiment["causal_intervention_contracts"][0]["source_causal_hypothesis_id"] == "h_state_b"
    assert "other" not in feedback["by_case"]


def test_candidate_contract_keeps_analyzer_counterfactual_separate() -> None:
    contracts = iterative_module._causal_intervention_contracts(
        [
            {
                "action_id": "a1",
                "action_group": "prompt",
                "operation": "modify",
                "intervention": "Persist the causal source state before deriving its result.",
                "expected_effect": "The next run writes the output.",
                "analyzer_counterfactual_predictions": ["Only the diagnosed decision changes before output creation."],
                "source_causal_hypothesis_id": "h1",
                "target_case_ids": ["case_001"],
            }
        ]
    )

    assert contracts[0]["predicted_behavior_and_outcome"] == "The next run writes the output."
    assert contracts[0]["intervention"] == "Persist the causal source state before deriving its result."
    assert contracts[0]["analyzer_counterfactual_predictions"] == [
        "Only the diagnosed decision changes before output creation."
    ]


def test_candidate_intervention_excerpt_uses_harness_mutation_when_task_patch_is_absent() -> None:
    excerpts = iterative_module._candidate_intervention_excerpts_by_case(
        [
            {
                "action_group": "prompt",
                "target_path": "system_prompt.md",
                "intervention": "Apply the required upstream state change before downstream validation.",
                "target_case_ids": ["case_001"],
            }
        ],
        {"case_001", "case_002"},
    )

    assert excerpts == {
        "case_001": (
            "[prompt:system_prompt.md]\nApply the required upstream state change before downstream validation."
        )
    }


def test_issue_signature_changes_when_only_residual_requirements_change(tmp_path: Path) -> None:
    def write_analysis(name: str, residual_ids: list[str]) -> Path:
        path = tmp_path / f"{name}.yaml"
        _write_yaml(
            path,
            {
                "issues": [
                    {
                        "issue_id": "issue_1",
                        "category": "member_harness",
                        "summary": "The requested output is only partially complete.",
                        "recommendation": "Continue from the verified partial result.",
                        "failure_mode": "partial_completion",
                        "affected_cases": ["case_1"],
                        "metadata": {
                            "attribution": {
                                "evidence_status": "confirmed",
                                "target_ref": "member_harness.policy_harness.prompt",
                                "causal_coverage": {
                                    "explained_requirement_ids": ["r1"],
                                    "residual_requirement_ids": residual_ids,
                                },
                            }
                        },
                    }
                ]
            },
        )
        return path

    first = iterative_module._analysis_issue_signatures(write_analysis("first", ["r2", "r3"]))
    same = iterative_module._analysis_issue_signatures(write_analysis("same", ["r3", "r2"]))
    repaired = iterative_module._analysis_issue_signatures(write_analysis("repaired", ["r3"]))

    assert first["issue_1"] == same["issue_1"]
    assert first["issue_1"] != repaired["issue_1"]


def test_compact_analysis_diagnoses_preserves_multiple_case_diagnoses(tmp_path: Path) -> None:
    diagnoses_path = tmp_path / "per_case_diagnoses.json"
    diagnoses_path.write_text(
        json.dumps(
            {
                "per_case_diagnoses": [
                    {
                        "case_id": "case_001",
                        "root_cause": "first independent failure",
                        "target_ref": "member_harness.solver.skill",
                        "prior_experiment_assessment": {
                            "intervention_activated": "unknown",
                            "predicted_behavior_occurred": "no",
                        },
                    },
                    {
                        "case_id": "case_001",
                        "root_cause": "second independent failure",
                        "target_ref": "member_harness.solver.tool",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    analysis_ref = tmp_path / "analysis_ref.yaml"
    _write_yaml(
        analysis_ref,
        {"metadata": {"per_case_diagnoses_path": str(diagnoses_path)}},
    )

    compact = iterative_module._compact_analysis_diagnoses(analysis_ref)

    assert [item["root_cause"] for item in compact["case_001"]] == [
        "first independent failure",
        "second independent failure",
    ]
    state = {
        "optimization_journal": [
            {
                "experiment_id": "e001-b001-a1",
                "verifier_deltas_by_case": {"case_001": {}},
                "candidate_failure_diagnoses": compact,
            }
        ]
    }
    feedback = iterative_module._prior_candidate_feedback(
        state,
        [{"case_id": "case_001"}],
    )["by_case"]["case_001"][0]
    assert feedback["candidate_failure_diagnosis"]["root_cause"] == "first independent failure"
    assert len(feedback["candidate_failure_diagnoses"]) == 2
    assert compact["case_001"][0]["prior_experiment_assessment"]["predicted_behavior_occurred"] == "no"


def test_failed_candidate_behavior_materializes_same_route_repair_issue(tmp_path: Path) -> None:
    analysis_ref = tmp_path / "analysis_ref.yaml"
    issues_path = tmp_path / "issues.yaml"
    _write_yaml(analysis_ref, {"issues": [], "issues_path": str(issues_path)})
    _write_yaml(issues_path, {"issues": []})

    iterative_module._materialize_candidate_activation_repair(
        analysis_ref,
        capabilities=[
            {
                "role": "solver",
                "action_group": "prompt",
                "operation": "modify",
            }
        ],
        causal_intervention_contracts=[
            {
                "intervention": "On trigger A, perform B and verify C.",
                "predicted_behavior_and_outcome": "B and C are visible in the trace.",
            }
        ],
        diagnoses_by_case={
            "case_001": [
                {
                    "prior_experiment_assessment": {
                        "availability": "available",
                        "intervention_activated": "unknown",
                        "predicted_behavior_occurred": "no",
                        "predicted_outcome_occurred": "no",
                    }
                }
            ]
        },
    )

    issue = iterative_module._read_yaml(analysis_ref)["issues"][0]
    attribution = issue["metadata"]["attribution"]
    assert issue["optimization_target"] == "member_harness"
    assert attribution["target_ref"] == "member_harness.solver.prompt_section"
    assert attribution["evidence_status"] == "confirmed"
    assert attribution["hypothesis_assessment"][0]["verification_status"] == "verified"
    assert attribution["hypothesis_assessment"][0]["verification_basis"] == "paired_candidate_experiment"
    assert attribution["decision_contract"]["acceptance_observable"] == "B and C are visible in the trace."
    assert iterative_module._read_yaml(issues_path)["issues"][0]["issue_id"] == issue["issue_id"]
    hypotheses_path = iterative_module.compile_optimization_hypotheses(
        analysis_ref_path=str(analysis_ref),
        cases=[{"case_id": "case_001", "task": "Complete the requested deliverable."}],
        output_path=tmp_path / "optimization_hypotheses.yaml",
    )
    hypotheses = iterative_module.load_optimization_hypotheses(hypotheses_path)
    assert len(hypotheses) == 1
    assert hypotheses[0]["source_issue_id"] == issue["issue_id"]


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


def test_jiuwenswarm_type_steps_count_successful_skill_before_code_edit() -> None:
    from openjiuwen.rsi.evaluator.trajectory_usage import (
        collect_pre_edit_successful_usage,
        collect_successful_skill_names,
    )

    trajectory = {
        "steps": [
            {
                "type": "tool",
                "detail": {
                    "tool_name": "skill_tool",
                    "call_args": {
                        "skill_name": "spreadsheet_delivery_preflight",
                        "relative_file_path": "SKILL.md",
                    },
                    "call_result": "success=True data={'skill_content': 'ok'} error=None",
                },
            },
            {
                "type": "tool",
                "detail": {
                    "tool_name": "code",
                    "call_args": {"code": "workbook.save('/workspace/output/result.xlsx')"},
                    "call_result": "success=True data={} error=None",
                },
            },
        ]
    }
    all_names: set[str] = set()
    pre_edit_names: set[str] = set()

    collect_successful_skill_names(trajectory, all_names)
    first_edit = collect_pre_edit_successful_usage(
        trajectory,
        skill_names=pre_edit_names,
    )

    assert all_names == {"spreadsheet_delivery_preflight"}
    assert pre_edit_names == {"spreadsheet_delivery_preflight"}
    assert first_edit == 1


def test_normalized_message_trace_counts_successful_skill_before_edit(tmp_path: Path) -> None:
    from openjiuwen.rsi.single_harness.iterative import (
        _invoked_skill_names,
        _pre_edit_invoked_names_by_case,
    )

    normalized_path = tmp_path / "normalized_trace.json"
    normalized_path.write_text(
        json.dumps(
            {
                "traces": [
                    {
                        "messages": [
                            {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": "skill_tool",
                                        "input": json.dumps({"skill_name": "spreadsheet_fidelity"}),
                                        "output": "success=True data={'skill_content': 'ok'} error=None",
                                        "error": "",
                                    }
                                ],
                            },
                            {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": "bash",
                                        "input": json.dumps(
                                            {"command": "python -c \"open('result.txt', 'w').write('ok')\""}
                                        ),
                                        "output": "success=True data={} error=None",
                                        "error": "",
                                    }
                                ],
                            },
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps({"behavior_trace": {"normalized_trace_path": str(normalized_path)}}),
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
                    "status": "failed",
                    "score": 0.0,
                    "trace_path": str(trace_path),
                    "result_path": str(result_path),
                }
            ]
        },
    )

    pre_edit, first_edit = _pre_edit_invoked_names_by_case(str(eval_ref), action_group="skill")

    assert _invoked_skill_names(str(eval_ref)) == {"spreadsheet_fidelity"}
    assert pre_edit == {"case_001": {"spreadsheet_fidelity"}}
    assert first_edit == {"case_001": 1}


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
    assert gate["causal_failure_class"] == "intervention_not_activated"
    assert gate["expected_skill_names"] == ["post_edit_validation"]
    assert gate["invoked_skill_names"] == []
    assert gate["missing_expected_skill_names"] == ["post_edit_validation"]


def test_candidate_gate_reports_evaluation_error_before_missing_skill(
    tmp_path: Path,
) -> None:
    class ErrorEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "error",
                            "score": 0.0,
                        }
                    ]
                },
            )
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(source_eval, {"cases": [{"case_id": "case_001", "score": 0.0}]})
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            evaluator=EvaluatorConfig(backend="single_harness"),
        ),
        evaluator=ErrorEvaluator(),
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
    assert gate["status"] == "inconclusive"
    assert gate["reason"] == "candidate_gate_inconclusive_due_to_error_cases"


def test_candidate_gate_records_evaluator_exception_as_inconclusive(tmp_path: Path) -> None:
    class RaisingEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            del kwargs
            raise TimeoutError("judge timed out with api_key=sk-1234567890abcdef")

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(source_eval, {"cases": [{"case_id": "case_001", "score": 0.0}]})
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(evaluator=EvaluatorConfig(backend="single_harness")),
        evaluator=RaisingEvaluator(),
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
            capabilities=[{"action_group": "prompt", "operation": "modify"}],
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
    assert gate["status"] == "inconclusive"
    assert gate["reason"] == "candidate_evaluation_failed"
    assert gate["candidate_evaluation_error"]["error_type"] == "TimeoutError"
    assert "sk-1234567890abcdef" not in gate["candidate_evaluation_error"]["message"]


def test_candidate_gate_requires_every_skill_and_tool_in_multi_action_plan(tmp_path: Path) -> None:
    class MultiCapabilityEvaluator:
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
                        "tool_name": "skill_tool",
                        "call_args": json.dumps({"skill_name": skill_name}),
                        "call_result": {"success": True},
                    },
                }
                for skill_name in ["invoice_rules", "style_guide"]
            ] + [
                {
                    "kind": "tool",
                    "error": None,
                    "detail": {
                        "tool_name": tool_name,
                        "call_args": "{}",
                        "call_result": {"success": True},
                    },
                }
                for tool_name in ["formatter", "validator"]
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
        {"cases": [{"case_id": "case_001", "status": "failed", "score": 0.0}]},
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(evaluator=EvaluatorConfig(backend="single_harness")),
        evaluator=MultiCapabilityEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )
    capabilities = [
        {
            "action_group": action_group,
            "operation": "add",
            "runtime_name": runtime_name,
            "target_case_ids": ["case_001"],
        }
        for action_group, runtime_name in [
            ("skill", "invoice_rules"),
            ("skill", "style_guide"),
            ("tool", "formatter"),
            ("tool", "validator"),
        ]
    ]

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=capabilities,
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
    assert gate["expected_skill_names"] == ["invoice_rules", "style_guide"]
    assert gate["expected_tool_names"] == ["formatter", "validator"]
    assert gate["missing_expected_skill_names"] == []
    assert gate["missing_expected_tool_names"] == []


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


def test_candidate_gate_ignores_unrelated_source_error(tmp_path: Path) -> None:
    class TargetEvaluator:
        def __init__(self) -> None:
            self.case_ids: list[str] = []

        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            self.case_ids = [str(case["case_id"]) for case in kwargs["cases"]]
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {"cases": [{"case_id": case_id, "status": "passed", "score": 1.0} for case_id in self.case_ids]},
            )
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [
                {"case_id": "target", "status": "failed", "score": 0.0},
                {"case_id": "unrelated", "status": "error", "score": 0.0},
            ]
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    cases = [
        {"case_id": "target", "input": "fix target"},
        {"case_id": "unrelated", "input": "unavailable"},
    ]
    dataset_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    evaluator = TargetEvaluator()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(evaluator=EvaluatorConfig(backend="single_harness")),
        evaluator=evaluator,
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
            capabilities=[
                {
                    "action_group": "prompt",
                    "operation": "modify",
                    "runtime_name": "prompt",
                    "target_case_ids": ["target"],
                }
            ],
            output_dir=tmp_path / "candidate_eval",
            dataset=SimpleNamespace(
                dataset_id="test",
                dataset_dir=str(dataset_path.parent),
                dataset_files=[str(dataset_path)],
                cases=2,
            ),
            frozen_target_case_ids={"target"},
        )
    )

    assert gate["accepted"] is True
    assert evaluator.case_ids == ["target"]


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
