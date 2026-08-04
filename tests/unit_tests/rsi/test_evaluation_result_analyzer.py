# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contract tests for evaluation-result analyzer."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml


class _FakeExperienceLearner:
    """Capture analyzer experience lookups without touching persistence."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def retrieve_member_stage_experience(self, **kwargs: Any) -> Any:
        from openjiuwen.rsi.optimization_experience_learner.schema import (
            OptimizationExperienceRetrievalResult,
        )

        self.calls.append(kwargs)
        query = type("Query", (), {"stage": kwargs["stage"]})()
        return OptimizationExperienceRetrievalResult(
            query=query,
            matches=[{"experience_id": "analysis_exp_001"}],
            metadata={"retrieval_status": "mocked"},
        )


class _FakeIssueStrategy:
    async def analyze(self, invocation):  # type: ignore[no-untyped-def]
        from openjiuwen.rsi.schema import (
            EvaluationResultAnalysisArtifact,
            TeamIssue,
        )

        return EvaluationResultAnalysisArtifact(
            analysis_id="analysis",
            analysis_ref_path="",
            issues=[
                TeamIssue(
                    issue_id="issue_001",
                    category="member_harness",
                    severity="high",
                    summary="solver missed the required output",
                    affected_cases=["case_001"],
                    suspected_team_scope="member",
                    recommendation="add a completion rule",
                    metadata={
                        "attribution": {
                            "root_cause": "missing completion gate",
                            "target_ref": "member_harness.prompt_section",
                            "evidence_refs": [],
                        }
                    },
                )
            ],
            metadata={"analysis_status": "completed"},
        )


class TestAnalyzerConfiguration:
    """Analyzer configuration and public protocol contracts."""

    def test_config_parses_diagnosis_agent_fields(self) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig

        config = EvaluationResultAnalyzerConfig.from_dict(
            {
                "model_config_ref": "models/analyzer.yaml",
                "diagnosis_agent_model_config_ref": "models/diagnosis.yaml",
                "diagnosis_agent_max_retries": 3,
                "diagnosis_agent_max_concurrency": 7,
                "diagnosis_agent_max_iterations": 25,
                "max_issues": 8,
                "evidence_limit_per_issue": 3,
                "output_filename": "issues.yaml",
            }
        )

        assert config.model_config_ref == "models/analyzer.yaml"
        assert config.diagnosis_agent_model_config_ref == "models/diagnosis.yaml"
        assert config.diagnosis_agent_max_retries == 3
        assert config.diagnosis_agent_max_concurrency == 7
        assert config.diagnosis_agent_max_iterations == 25
        assert config.max_issues == 8
        assert config.evidence_limit_per_issue == 3
        assert config.output_filename == "issues.yaml"

    def test_interfaces_expose_strategy_and_signal_extractor_protocols(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.interfaces import (
            EvaluationResultAnalysisStrategy,
            SignalExtractor,
        )

        strategy_parameters = list(inspect.signature(EvaluationResultAnalysisStrategy.analyze).parameters)
        extractor_parameters = list(inspect.signature(SignalExtractor.extract).parameters)

        assert strategy_parameters == ["self", "invocation"]
        assert extractor_parameters == ["self", "summary", "case_inputs"]


class TestTeamIssueMapping:
    """Contracts for mapping raw diagnosis output to optimization targets."""

    def test_member_issue_uses_affected_components_as_target_members(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_001",
                "category": "member_harness",
                "severity": "high",
                "summary": "student-facing explanation is wrong",
                "affected_cases": ["case_001"],
                "affected_components": ["math_teacher"],
                "evidence": [
                    {
                        "case_id": "case_001",
                        "failure_mode": "role_spec_unclear",
                        "decisive_step": "trajectories/math_teacher.jsonl#step_4",
                    }
                ],
                "suspected_team_scope": "member",
                "recommendation": "tighten the math teacher role contract",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "member_harness"
        assert mapped.target_members == ["math_teacher"]
        assert mapped.metadata["affected_components"] == ["math_teacher"]

    def test_member_issue_without_member_evidence_does_not_default_to_team_leader(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_002",
                "category": "member_harness",
                "severity": "medium",
                "summary": "member-level issue lacks a reliable member anchor",
                "affected_cases": ["case_001"],
                "evidence": [{"case_id": "case_001", "failure_mode": "unknown"}],
                "suspected_team_scope": "member",
                "recommendation": "collect stronger member-level evidence",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "member_harness"
        assert mapped.target_members == []

    def test_member_issue_extracts_target_member_from_roleful_target_ref(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_004",
                "category": "member_harness",
                "severity": "high",
                "summary": "builder skipped required artifact verification",
                "affected_cases": ["case_001"],
                "suspected_team_scope": "member",
                "target_ref": "member_harness.builder.skill",
                "evidence_refs": [
                    {
                        "trace_id": "case_001__builder__trajectory",
                        "role": "builder",
                        "message_index": 0,
                    }
                ],
                "recommendation": "tighten builder completion verification",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "member_harness"
        assert mapped.target_members == ["builder"]

    def test_member_harness_team_target_ref_routes_to_team_skill(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_team_protocol",
                "category": "member_harness",
                "severity": "high",
                "summary": "team completed the task board before required files existed",
                "affected_cases": ["case_001"],
                "suspected_team_scope": "member",
                "target_ref": "member_harness.team.prompt",
                "evidence_refs": [
                    {
                        "trace_id": "case_001__team__case",
                        "role": "team",
                        "message_index": 0,
                        "step_pointer": "claim_task",
                    }
                ],
                "recommendation": "separate task-board completion from deliverable verification",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "team_skill"
        assert mapped.target_members == []
        assert mapped.category == "team_coordination"
        assert mapped.suspected_team_scope == "team_skill"
        assert mapped.metadata["attribution"]["target_ref"] == "team_skill.team_leader.constraint_violation"

    def test_team_leader_member_target_ref_routes_to_team_skill(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_leader_completion",
                "category": "member_harness",
                "severity": "high",
                "summary": "leader marked final task completed without collected artifacts",
                "affected_cases": ["case_001"],
                "suspected_team_scope": "member",
                "target_ref": "member_harness.team_leader.prompt",
                "evidence_refs": [
                    {
                        "trace_id": "case_001__team_leader__trajectory",
                        "role": "team_leader",
                        "message_index": 0,
                        "step_pointer": "claim_task",
                    }
                ],
                "recommendation": "add team-level completion gate before final completion",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "team_skill"
        assert mapped.target_members == []
        assert mapped.category == "team_coordination"
        assert mapped.metadata["attribution"]["target_ref"] == "team_skill.team_leader.constraint_violation"

    def test_member_scope_wins_over_team_coordination_category(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_003",
                "category": "team_coordination",
                "severity": "medium",
                "summary": "executor missed verifier feedback in a handoff step",
                "affected_cases": ["case_001"],
                "evidence": [
                    {
                        "case_id": "case_001",
                        "affected_component": "executor",
                        "failure_mode": "handoff_feedback_lost",
                    }
                ],
                "suspected_team_scope": "member",
                "recommendation": "tighten executor feedback handling",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "member_harness"
        assert mapped.target_members == ["executor"]

    def test_target_ref_scope_wins_over_conflicting_member_scope(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_team_skill_ref",
                "category": "member_harness",
                "severity": "high",
                "summary": "role handoff lost required page constraints",
                "affected_cases": ["case_001"],
                "suspected_team_scope": "member",
                "target_ref": "team_skill.planner.role_coordination",
                "evidence_refs": [
                    {
                        "trace_id": "case_001__planner__trajectory",
                        "role": "planner",
                        "message_index": 0,
                    }
                ],
                "recommendation": "update team skill planner handoff policy",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "team_skill"
        assert mapped.target_members == []

    def test_target_ref_scope_wins_over_conflicting_team_scope(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_member_ref",
                "category": "team_coordination",
                "severity": "high",
                "summary": "builder lacked a reusable verification method",
                "affected_cases": ["case_001"],
                "suspected_team_scope": "team_skill",
                "target_ref": "member_harness.builder.skill",
                "evidence_refs": [
                    {
                        "trace_id": "case_001__builder__trajectory",
                        "role": "builder",
                        "message_index": 0,
                    }
                ],
                "recommendation": "add a builder verification skill",
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == "member_harness"
        assert mapped.target_members == ["builder"]

    def test_unassigned_attribution_does_not_open_optimizer_gate(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_unassigned",
                "category": "member_harness",
                "severity": "low",
                "summary": "current evidence is too weak to select a harness variable",
                "affected_cases": ["case_001"],
                "suspected_team_scope": "member",
                "metadata": {
                    "attribution": {
                        "target_ref": "unassigned",
                        "confidence": "low",
                    }
                },
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == ""
        assert mapped.target_members == []

    def test_evidence_pipeline_failure_does_not_open_member_optimizer_gate(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _apply_g5_mapping,
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_trace_missing",
                "category": "member_harness",
                "severity": "medium",
                "summary": "trajectory_events.jsonl is missing from the case evidence directory",
                "affected_cases": ["case_001"],
                "suspected_team_scope": "member",
                "recommendation": "do not modify member_harness.workflow; fix evaluator evidence output",
                "metadata": {
                    "attribution": {
                        "target_ref": "member_harness.workflow",
                        "root_cause": "No such file or directory: trajectory_events.jsonl",
                        "confidence": "medium",
                    }
                },
            }
        )

        mapped = _apply_g5_mapping(issue)

        assert mapped.optimization_target == ""
        assert mapped.target_members == []


class TestCaseReader:
    """Filesystem reader contracts for evaluation artifacts."""

    def test_reads_eval_ref_summary_and_case_inputs(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseReader,
            EvaluationSummaryInput,
        )

        artifacts = _write_evaluation_artifacts(tmp_path, method="llm_as_judge")
        reader = CaseReader()

        eval_ref = reader.read_eval_ref(str(artifacts["eval_ref_path"]))
        summary = reader.read_summary(eval_ref["summary_path"])
        case_inputs = reader.read_case_inputs(str(artifacts["case_results_dir"]))

        assert eval_ref["eval_id"] == "eval_001"
        assert summary == EvaluationSummaryInput(
            total_cases=1,
            passed_count=0,
            failed_count=1,
            average_score=0.42,
            evaluation_method="llm_as_judge",
        )
        assert len(case_inputs) == 1
        assert case_inputs[0].case_id == "case_001"
        assert case_inputs[0].input == "solve 1 + 1"
        assert case_inputs[0].response == "3"
        assert case_inputs[0].evaluation_metadata["parsed"]["dimensions"]["avg_behavior_score"] == 0.42
        assert case_inputs[0].training_signal["capability_gap"] == "needs deterministic arithmetic validation"

    def test_reads_authoritative_test_contract_without_solution_patch(
        self,
        tmp_path: Path,
    ) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
            _build_evidence_summary,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseReader,
            DeterministicSignals,
        )

        dataset_path = tmp_path / "cases.json"
        _write_json(
            dataset_path,
            {
                "cases": [
                    {
                        "case_id": "case_001",
                        "verification_contract": {
                            "fail_to_pass": ["tests/test_contract.py::test_boundary"],
                            "pass_to_pass": ["tests/test_contract.py::test_existing"],
                            "test_patch": "+ assert EXPECTED_TEST_ASSERTION",
                            "patch": "SECRET_GOLD_PATCH",
                        },
                    }
                ]
            },
        )
        case_dir = tmp_path / "case_results" / "case_001"
        case_dir.mkdir(parents=True)
        _write_json(
            case_dir / "result.json",
            {
                "case_id": "case_001",
                "status": "failed",
                "score": 0.0,
                "evaluation": {
                    "method": "swebench_official",
                    "passed": False,
                    "reason": "FAIL_TO_PASS failed",
                    "metadata": {},
                },
                "metadata": {"case_path": str(dataset_path)},
            },
        )
        _write_json(
            case_dir / "trace.json",
            {"input": "fix the contract", "response": "attempted fix"},
        )

        case = CaseReader().read_case_inputs(str(tmp_path / "case_results"))[0]

        assert case.benchmark_test_contract["fail_to_pass"] == ["tests/test_contract.py::test_boundary"]
        assert case.benchmark_test_contract["pass_to_pass"] == ["tests/test_contract.py::test_existing"]
        assert case.benchmark_test_contract["test_patch"] == ("+ assert EXPECTED_TEST_ASSERTION")
        assert "patch" not in case.benchmark_test_contract
        summary = _build_evidence_summary(case)
        assert "Authoritative Benchmark Test Contract" in summary
        assert "EXPECTED_TEST_ASSERTION" in summary
        assert "SECRET_GOLD_PATCH" not in summary
        diagnosis_input = json.loads(
            _build_diagnosis_input_json(
                case=case,
                signals=DeterministicSignals(),
                retrieved_experience=None,
                evidence_summary_available=True,
            )
        )
        assert diagnosis_input["authoritative_benchmark_test_contract"] == (case.benchmark_test_contract)
        assert "SECRET_GOLD_PATCH" not in json.dumps(diagnosis_input)

    def test_missing_case_results_dir_returns_empty_inputs(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import CaseReader

        reader = CaseReader()

        assert reader.read_case_inputs(str(tmp_path / "missing_case_results")) == []

    def test_missing_eval_ref_raises_value_error(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import CaseReader

        reader = CaseReader()

        with pytest.raises(ValueError):
            reader.read_eval_ref(str(tmp_path / "missing_eval_ref.yaml"))


class TestSignalExtractors:
    """Method-aware deterministic signal extraction contracts."""

    def test_build_signal_extractor_dispatches_by_method(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
            GenericSignalExtractor,
            LlmJudgeSignalExtractor,
            PytestSignalExtractor,
            build_signal_extractor,
        )

        assert isinstance(build_signal_extractor("default"), GenericSignalExtractor)
        assert isinstance(build_signal_extractor("exact_match"), GenericSignalExtractor)
        assert isinstance(build_signal_extractor("script_based"), PytestSignalExtractor)
        assert isinstance(build_signal_extractor("llm_as_judge"), LlmJudgeSignalExtractor)
        assert isinstance(build_signal_extractor("unknown_method"), GenericSignalExtractor)

    def test_generic_extractor_reports_common_failures_and_expected_mismatch(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
            GenericSignalExtractor,
        )

        summary = _summary_input(method="exact_match")
        case_inputs = [
            _case_input(
                case_id="case_001",
                method="exact_match",
                status="passed",
                passed=False,
                expected="2",
                response="3",
            ),
            _case_input(
                case_id="case_002",
                method="exact_match",
                status="failed",
                passed=False,
                error="ValueError at C:/tmp/run_123.py:44",
            ),
        ]

        signals = GenericSignalExtractor().extract(summary, case_inputs)

        assert signals.method == "exact_match"
        assert signals.exec_failures == ["case_002"]
        assert signals.judge_failures == ["case_001"]
        assert signals.method_specific["expected_mismatch_cases"] == ["case_001"]
        assert signals.error_clusters

    def test_pytest_extractor_falls_back_when_pytest_evidence_is_missing(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
            PytestSignalExtractor,
        )

        case_inputs = [
            _case_input(
                case_id="case_001",
                method="script_based",
                status="passed",
                passed=False,
                metadata={},
            )
        ]

        signals = PytestSignalExtractor().extract(_summary_input(method="script_based"), case_inputs)

        assert signals.method == "script_based"
        assert signals.judge_failures == ["case_001"]
        assert signals.method_specific["evidence_missing_cases"] == ["case_001"]
        assert signals.method_specific["fallback_reason"] == "pytest_evidence_missing"

    def test_reward_trace_seed_uses_real_role_without_solver_fallback(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
            RewardSignalExtractor,
        )

        case_inputs = [
            _case_input(
                case_id="case_001",
                method="reward_based",
                status="passed",
                passed=False,
                metadata={"reward": 0.0},
                normalized_trace_summary={
                    "traces": [
                        {
                            "trace_id": "case_001__content_writer__trajectory",
                            "member_id": "content_writer",
                            "member_role": "content_writer",
                            "messages": [
                                {
                                    "message_index": 0,
                                    "tool_calls": [
                                        {
                                            "name": "bash",
                                            "input": "pytest",
                                            "error": "assertion failed",
                                            "step_pointer": "step_001",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        ]

        signals = RewardSignalExtractor().extract(
            _summary_input(method="reward_based"),
            case_inputs,
        )

        attribution = signals.method_specific["normalized_trace_attribution_by_case"]["case_001"]
        assert attribution["target_ref"] == "member_harness.content_writer.skill"
        assert attribution["evidence_refs"][0]["role"] == "content_writer"

    def test_reward_trace_seed_does_not_invent_solver_role(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
            RewardSignalExtractor,
        )

        case_inputs = [
            _case_input(
                case_id="case_001",
                method="reward_based",
                status="passed",
                passed=False,
                metadata={"reward": 0.0},
                normalized_trace_summary={
                    "traces": [
                        {
                            "trace_id": "case_001__team__case",
                            "member_id": "default_team",
                            "member_role": "team",
                            "messages": [
                                {
                                    "message_index": 0,
                                    "tool_calls": [
                                        {
                                            "name": "bash",
                                            "input": "pytest",
                                            "error": "assertion failed",
                                            "step_pointer": "step_001",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        ]

        signals = RewardSignalExtractor().extract(
            _summary_input(method="reward_based"),
            case_inputs,
        )

        attribution = signals.method_specific["normalized_trace_attribution_by_case"]["case_001"]
        assert attribution["target_ref"] == "unassigned"
        assert attribution["confidence"] == "low"
        assert attribution["evidence_refs"][0]["role"] == ""

    def test_llm_judge_extractor_reads_parsed_dimensions_without_llm(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
            LlmJudgeSignalExtractor,
        )

        case_inputs = [
            _case_input(
                case_id="case_001",
                method="llm_as_judge",
                status="passed",
                passed=False,
                metadata={
                    "parsed": {
                        "dimensions": {
                            "per_behavior_scores": {"b1": 0.2, "b2": 0.8},
                            "low_score_behaviors": ["b1"],
                            "avg_behavior_score": 0.5,
                            "pass_count": 1,
                            "fail_count": 1,
                            "behavior_diagnostics": {
                                "b1": {
                                    "reason": "missing artifact",
                                    "failure_reason": "no PPT artifact was generated",
                                    "missing_capability": "artifact validation",
                                    "suggested_surface_hint": "tool",
                                    "evidence": "artifacts/",
                                }
                            },
                        }
                    }
                },
            )
        ]

        signals = LlmJudgeSignalExtractor().extract(_summary_input(method="llm_as_judge"), case_inputs)

        assert signals.method == "llm_as_judge"
        assert signals.method_specific["low_score_behaviors"] == {"case_001": ["b1"]}
        assert signals.method_specific["behavior_score_distribution"] == {"case_001": {"b1": 0.2, "b2": 0.8}}
        assert signals.method_specific["behavior_diagnostics"] == {
            "case_001": {
                "b1": {
                    "reason": "missing artifact",
                    "failure_reason": "no PPT artifact was generated",
                    "missing_capability": "artifact validation",
                    "suggested_surface_hint": "tool",
                    "evidence": "artifacts/",
                }
            }
        }
        assert signals.method_specific["avg_behavior_score"] == {"case_001": 0.5}
        assert signals.method_specific["behavior_pass_fail_counts"] == {"case_001": {"pass_count": 1, "fail_count": 1}}

    def test_llm_judge_extractor_falls_back_when_dimensions_are_missing(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
            LlmJudgeSignalExtractor,
        )

        case_inputs = [
            _case_input(
                case_id="case_001",
                method="llm_as_judge",
                status="passed",
                passed=False,
                metadata={"parsed": {}},
            )
        ]

        signals = LlmJudgeSignalExtractor().extract(_summary_input(method="llm_as_judge"), case_inputs)

        assert signals.judge_failures == ["case_001"]
        assert signals.method_specific["rationale_missing_cases"] == ["case_001"]
        assert signals.method_specific["fallback_reason"] == "parsed_dimensions_missing"


class TestDiagnosisAgentStrategy:
    """DeepAgent strategy factory and normalization contracts."""

    def test_build_analysis_strategy_returns_diagnosis_agent_strategy(self) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import build_analysis_strategy

        strategy = build_analysis_strategy(EvaluationResultAnalyzerConfig())

        assert strategy.name == "diagnosis_agent"

    def test_strategy_protocol_accepts_single_invocation(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.interfaces import (
            EvaluationResultAnalysisStrategy,
        )

        parameters = list(inspect.signature(EvaluationResultAnalysisStrategy.analyze).parameters)

        assert parameters == ["self", "invocation"]

    @pytest.mark.asyncio
    async def test_build_agent_expands_model_config_env_vars(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

        model_config = tmp_path / "model.yaml"
        model_config.write_text(
            "\n".join(
                [
                    "model_client_config:",
                    "  client_provider: OpenAI",
                    "  api_key: ${ANALYZER_TEST_KEY}",
                    "  api_base: https://example.invalid/v1",
                    "model_request_config:",
                    "  model: glm-5.1",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("ANALYZER_TEST_KEY", "expanded-key")
        captured: dict[str, Any] = {}

        class FakeTeamModelConfig:
            @classmethod
            def model_validate(cls, data: dict[str, Any]) -> "FakeTeamModelConfig":
                captured["model_data"] = data
                return cls()

            def build(self) -> str:
                return "fake-model"

        def fake_create_deep_agent(**kwargs: Any) -> dict[str, Any]:
            captured["agent_kwargs"] = kwargs
            return kwargs

        monkeypatch.setattr(analyzer_module, "TeamModelConfig", FakeTeamModelConfig)
        monkeypatch.setattr(analyzer_module, "create_deep_agent", fake_create_deep_agent)

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref=str(model_config))
        )

        agent = await strategy._build_agent(str(tmp_path))

        assert captured["model_data"]["model_client_config"]["api_key"] == "expanded-key"
        assert agent["model"] == "fake-model"
        rails = captured["agent_kwargs"]["rails"]
        assert len(rails) == 1
        assert isinstance(rails[0], analyzer_module.RSISysOperationRail)
        assert rails[0]._read_only is True
        assert rails[0]._bash_pipefail is True

    @pytest.mark.asyncio
    async def test_analyze_diagnoses_only_nonpassing_cases(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.schema import (
            EvaluationResultAnalysisInvocation,
        )

        case_results_dir = tmp_path / "cases"
        for case_id, passed in (("solved", True), ("unresolved", False)):
            case_dir = case_results_dir / case_id
            case_dir.mkdir(parents=True)
            (case_dir / "result.json").write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "status": "passed" if passed else "failed",
                        "score": 1.0 if passed else 0.0,
                        "evaluation": {
                            "method": "swebench_official",
                            "passed": passed,
                            "reason": "",
                            "metadata": {},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (case_dir / "trace.json").write_text("{}", encoding="utf-8")
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "total_cases": 2,
                    "passed_cases": 1,
                    "failed_cases": 1,
                    "average_score": 0.5,
                    "evaluation_method": "swebench_official",
                }
            ),
            encoding="utf-8",
        )
        eval_ref_path = tmp_path / "eval_ref.yaml"
        eval_ref_path.write_text(
            yaml.safe_dump({"summary_path": str(summary_path)}),
            encoding="utf-8",
        )
        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"),
            experience_learner=_FakeExperienceLearner(),
        )
        diagnosed_case_ids: list[str] = []

        async def fake_per_case_diagnosis(
            case_inputs: list[Any],
            *args: Any,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            diagnosed_case_ids.extend(case.case_id for case in case_inputs)
            return []

        monkeypatch.setattr(strategy, "_per_case_diagnosis", fake_per_case_diagnosis)
        artifact = await strategy.analyze(
            EvaluationResultAnalysisInvocation(
                eval_ref_path=str(eval_ref_path),
                case_results_dir=str(case_results_dir),
                case_traces_dir=str(case_results_dir),
                team_skill_ref_path="",
                harness_refs_path="",
                output_dir=str(tmp_path / "analysis"),
            )
        )

        assert diagnosed_case_ids == ["unresolved"]
        assert artifact.metadata["per_case_count"] == 2
        assert artifact.metadata["diagnosed_case_count"] == 1

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_uses_case_dir_as_workspace(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(
                model_config_ref="unused.yaml",
                diagnosis_agent_max_concurrency=2,
            ),
            experience_learner=_FakeExperienceLearner(),
        )
        case_inputs = []
        expected_case_dirs: list[Path] = []
        for case_id in ["case_001", "case_002"]:
            case_dir = tmp_path / "case_results" / case_id
            result_path = case_dir / "result.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text("{}", encoding="utf-8")
            # normalized_trace.json lives under judge/ inside the case dir.
            # No artifacts/ directory is required for analyzer evidence prep.
            trace_path = case_dir / "judge" / "normalized_trace.json"
            trace_path.parent.mkdir(parents=True)
            trace_path.write_text(json.dumps({"case_id": case_id, "traces": []}), encoding="utf-8")
            expected_case_dirs.append(case_dir)
            case_inputs.append(
                CaseAnalysisInput(
                    case_id=case_id,
                    status="failed",
                    score=0.0,
                    input="input",
                    expected=None,
                    response="response",
                    error="",
                    evaluation_method="llm_as_judge",
                    evaluation_passed=False,
                    evaluation_reason="failed",
                    evaluation_metadata={},
                    trace_path=str(case_dir / "trace.json"),
                    result_path=str(result_path),
                )
            )

        build_workspaces: list[Path] = []

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            workspace_path = Path(workspace)
            build_workspaces.append(workspace_path)
            # workspace must be a bounded diagnosis runtime, not the raw case dir.
            assert workspace_path.is_dir()
            assert (workspace_path / "evidence_summary.md").is_file()
            assert not (workspace_path / "result.json").exists()
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            assert agent["workspace"]
            assert max_retries == 20
            # Analyzer agent reads only bounded evidence, not raw trace/artifact dirs.
            assert "evidence_summary.md" in prompt
            assert "judge/normalized_trace.json" not in prompt
            assert "artifacts" not in prompt
            return json.dumps(
                {
                    "issue_category": "member_harness",
                    "severity": "medium",
                    "summary": "tool call failed repeatedly",
                    "failure_mode": "repeated_failed_tool_call",
                    "root_cause": "skill misconfigured",
                    "critical_mistake": "first tool call at message 3",
                    "general_mechanism": "add retry guard in skill config",
                    "target_ref": "member_harness.skill",
                    "evidence_refs": [{"trace_id": "t1", "role": "executor", "message_index": 3, "step_pointer": ""}],
                    "affected_components": ["executor"],
                    "recommendation": "fix skill config",
                    "confidence": "medium",
                }
            )

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        results = await strategy._per_case_diagnosis(
            case_inputs,
            DeterministicSignals(method="llm_as_judge"),
            None,
        )

        assert [result["case_id"] for result in results] == ["case_001", "case_002"]
        assert len(build_workspaces) == 2
        assert len({str(path) for path in build_workspaces}) == 2
        assert not any(path in expected_case_dirs for path in build_workspaces)
        assert not any(path.exists() for path in build_workspaces)

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_uses_isolated_evaluated_repository_snapshot(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        evaluated_workspace = tmp_path / "evaluated_workspace"
        (evaluated_workspace / "src").mkdir(parents=True)
        (evaluated_workspace / "src" / "field.py").write_text(
            "FORMAT = root.opts.datetimeformat\n",
            encoding="utf-8",
        )
        (evaluated_workspace / ".git").mkdir()
        (evaluated_workspace / ".git" / "config").write_text("secret", encoding="utf-8")
        (evaluated_workspace / "messages").mkdir()
        (evaluated_workspace / "messages" / "trace.json").write_text("{}", encoding="utf-8")

        case_dir = tmp_path / "case_results" / "case_001"
        result_path = case_dir / "result.json"
        patch_path = case_dir / "verifier" / "model.patch"
        patch_path.parent.mkdir(parents=True)
        patch_path.write_text("diff --git a/src/field.py b/src/field.py\n", encoding="utf-8")
        result_path.write_text(
            json.dumps(
                {
                    "workspace_dir": str(evaluated_workspace),
                    "evaluation": {"metadata": {"model_patch_path": str(patch_path)}},
                }
            ),
            encoding="utf-8",
        )
        case = CaseAnalysisInput(
            case_id="case_001",
            status="failed",
            score=0.0,
            input="Preserve the root schema format for nested fields.",
            expected=None,
            response="done",
            error="",
            evaluation_method="script_based",
            evaluation_passed=False,
            evaluation_reason="failed",
            evaluation_metadata={},
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )
        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml")
        )
        observed_runtime: Path | None = None

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            nonlocal observed_runtime
            observed_runtime = Path(workspace)
            assert (observed_runtime / "repository" / "src" / "field.py").read_text(
                encoding="utf-8"
            ) == "FORMAT = root.opts.datetimeformat\n"
            assert not (observed_runtime / "repository" / ".git").exists()
            assert not (observed_runtime / "repository" / "messages").exists()
            assert (observed_runtime / "source_patch.diff").is_file()
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            assert "repository/" in prompt
            return json.dumps(
                {
                    "issue_category": "unassigned",
                    "severity": "low",
                    "summary": "No repository discriminator selected a reusable mechanism.",
                    "failure_mode": "ambiguous_semantics",
                    "root_cause": "Competing mechanisms remain unresolved.",
                    "critical_mistake": "No supported attribution.",
                    "general_mechanism": "Investigate before optimizing.",
                    "target_ref": "unassigned",
                    "evidence_refs": [],
                    "affected_components": [],
                    "recommendation": "Keep the case unassigned.",
                    "confidence": "low",
                }
            )

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        results = await strategy._per_case_diagnosis(
            [case],
            DeterministicSignals(method="script_based"),
            None,
        )

        assert results[0]["target_ref"] == "unassigned"
        assert observed_runtime is not None and not observed_runtime.exists()
        assert (evaluated_workspace / "src" / "field.py").read_text(
            encoding="utf-8"
        ) == "FORMAT = root.opts.datetimeformat\n"

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_raises_when_agent_returns_non_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        case_dir = tmp_path / "case_results" / "case_001"
        result_path = case_dir / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        trace_path = case_dir / "judge" / "normalized_trace.json"
        trace_path.parent.mkdir(parents=True)
        trace_path.write_text(json.dumps({"case_id": "case_001", "traces": []}), encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_001",
            status="failed",
            score=0.0,
            input="input",
            expected=None,
            response="response",
            error="",
            evaluation_method="script_based",
            evaluation_passed=False,
            evaluation_reason="failed",
            evaluation_metadata={},
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml")
        )

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            return "Error code: 401 - invalid_api_key"

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        with pytest.raises(ValueError, match="per-case diagnosis output did not contain JSON"):
            await strategy._per_case_diagnosis(
                [case],
                DeterministicSignals(method="script_based"),
                None,
            )

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_runs_cases_sequentially(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        case_inputs = []
        for case_id in ["case_001", "case_002"]:
            case_dir = tmp_path / "case_results" / case_id
            result_path = case_dir / "result.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text("{}", encoding="utf-8")
            trace_path = case_dir / "judge" / "normalized_trace.json"
            trace_path.parent.mkdir(parents=True)
            trace_path.write_text(json.dumps({"case_id": case_id, "traces": []}), encoding="utf-8")
            case_inputs.append(
                CaseAnalysisInput(
                    case_id=case_id,
                    status="failed",
                    score=0.0,
                    input="input",
                    expected=None,
                    response="response",
                    error="",
                    evaluation_method="llm_as_judge",
                    evaluation_passed=False,
                    evaluation_reason="failed",
                    evaluation_metadata={},
                    trace_path=str(case_dir / "trace.json"),
                    result_path=str(result_path),
                )
            )

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(
                model_config_ref="unused.yaml",
                diagnosis_agent_max_concurrency=2,
            )
        )

        active_calls = 0
        max_active_calls = 0
        call_order: list[str] = []

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            nonlocal active_calls, max_active_calls
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            try:
                call_order.append(Path(agent["workspace"]).name)
                await asyncio.sleep(0.01)
                return json.dumps(
                    {
                        "issue_category": "member_harness",
                        "severity": "medium",
                        "summary": "case diagnosed",
                        "failure_mode": "missing_capability",
                        "root_cause": "capability missing",
                        "critical_mistake": "first decisive mistake",
                        "general_mechanism": "member needs a stronger skill",
                        "target_ref": "member_harness.skill",
                        "evidence_refs": [],
                        "affected_components": ["executor"],
                        "recommendation": "improve skill",
                        "confidence": "medium",
                    }
                )
            finally:
                active_calls -= 1

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        results = await strategy._per_case_diagnosis(
            case_inputs,
            DeterministicSignals(method="llm_as_judge"),
            None,
        )

        assert [result["case_id"] for result in results] == ["case_001", "case_002"]
        assert max_active_calls == 1
        assert call_order[0].startswith("case_001")
        assert call_order[1].startswith("case_002")

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_records_retryable_empty_output_without_aborting(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )
        from openjiuwen.rsi.model_call import RetryableModelOutputError

        case_dir = tmp_path / "case_results" / "case_001"
        result_path = case_dir / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        trace_path = case_dir / "judge" / "normalized_trace.json"
        trace_path.parent.mkdir(parents=True)
        trace_path.write_text(json.dumps({"case_id": "case_001", "traces": []}), encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_001",
            status="failed",
            score=0.0,
            input="input",
            expected=None,
            response="response",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=False,
            evaluation_reason="failed",
            evaluation_metadata={},
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml")
        )

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            raise RetryableModelOutputError("diagnosis agent model output is empty")

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        results = await strategy._per_case_diagnosis(
            [case],
            DeterministicSignals(method="llm_as_judge"),
            None,
        )

        assert len(results) == 1
        result = results[0]
        assert result["case_id"] == "case_001"
        assert result["analysis_failed"] is True
        assert result["failure_mode"] == "diagnosis_unavailable"
        assert result["target_ref"] == "unassigned"
        assert "model output is empty" in result["error"]

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_repairs_natural_language_then_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        case_dir = tmp_path / "case_results" / "stable_unique_id_for_case_002"
        result_path = case_dir / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        trace_path = case_dir / "judge" / "normalized_trace.json"
        trace_path.parent.mkdir(parents=True)
        trace_path.write_text(
            json.dumps({"case_id": "stable_unique_id_for_case_002", "traces": []}),
            encoding="utf-8",
        )
        case = CaseAnalysisInput(
            case_id="stable_unique_id_for_case_002",
            status="passed",
            score=0.9019,
            input="input",
            expected=None,
            response="response",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=True,
            evaluation_reason="passed",
            evaluation_metadata={},
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(
                model_config_ref="unused.yaml",
                diagnosis_agent_max_retries=1,
            )
        )

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        calls = 0

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return "Now let me analyze the evidence carefully.\n\n**Case overview:** score is 0.9019."
            assert "Previous diagnosis output was not valid JSON" in prompt
            return json.dumps(
                {
                    "issue_category": "unassigned",
                    "severity": "low",
                    "summary": "Case passed with only minor residual issues.",
                    "failure_mode": "minor_residual_gap",
                    "root_cause": "No decisive failing turn is visible.",
                    "critical_mistake": "No decisive mistake.",
                    "general_mechanism": "Keep current harness unchanged.",
                    "target_ref": "unassigned",
                    "evidence_refs": [],
                    "affected_components": [],
                    "recommendation": "Do not optimize from this passing case alone.",
                    "confidence": "low",
                },
                ensure_ascii=False,
            )

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        results = await strategy._per_case_diagnosis(
            [case],
            DeterministicSignals(method="llm_as_judge"),
            None,
        )

        assert calls == 2
        assert results[0]["case_id"] == "stable_unique_id_for_case_002"
        assert results[0]["target_ref"] == "unassigned"

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_repairs_deterministic_evidence_conflict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        case_dir = tmp_path / "case_results" / "case_conflict"
        result_path = case_dir / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        trace_path = case_dir / "judge" / "normalized_trace.json"
        trace_path.parent.mkdir(parents=True)
        trace_path.write_text(
            json.dumps({"case_id": "case_conflict", "traces": []}),
            encoding="utf-8",
        )
        case = CaseAnalysisInput(
            case_id="case_conflict",
            status="failed",
            score=0.0,
            input="input",
            expected=None,
            response="response",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=False,
            evaluation_reason="failed",
            evaluation_metadata={},
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )
        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml")
        )

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        prompts: list[str] = []

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            prompts.append(prompt)
            if len(prompts) == 1:
                return json.dumps(
                    {
                        "target_ref": "member_harness.solver.skill",
                        "summary": "unsupported mechanism",
                    }
                )
            assert max_retries == 0
            assert "valid JSON but contradicted deterministic evidence" in prompt
            assert "unsupported causal claim" in prompt
            return json.dumps(
                {
                    "issue_category": "unassigned",
                    "severity": "low",
                    "summary": "Evidence does not distinguish a reusable mechanism.",
                    "failure_mode": "insufficient_causal_evidence",
                    "root_cause": "The available observations do not separate hypotheses.",
                    "critical_mistake": "No evidence-backed mechanism can be selected.",
                    "general_mechanism": "Do not optimize from unresolved causal evidence.",
                    "target_ref": "unassigned",
                    "evidence_refs": [],
                    "affected_components": [],
                    "recommendation": "Collect a discriminator before changing the harness.",
                    "confidence": "low",
                }
            )

        conflict_calls = 0

        def fake_conflicts(*args: Any, **kwargs: Any) -> list[str]:
            nonlocal conflict_calls
            conflict_calls += 1
            return ["unsupported causal claim"] if conflict_calls == 1 else []

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
        monkeypatch.setattr(
            analyzer_module,
            "_diagnosis_validation_conflicts",
            fake_conflicts,
        )

        results = await strategy._per_case_diagnosis(
            [case],
            DeterministicSignals(method="llm_as_judge"),
            None,
        )

        assert len(prompts) == 2
        assert conflict_calls == 2
        assert results[0]["target_ref"] == "unassigned"
        assert "analysis_failed" not in results[0]

    @pytest.mark.asyncio
    async def test_analyze_propagates_aggregation_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.schema import EvaluationResultAnalysisInvocation

        summary_path = tmp_path / "summary.json"
        summary_path.write_text(
            json.dumps({"total_cases": 1, "passed_cases": 0, "failed_cases": 1}),
            encoding="utf-8",
        )
        eval_ref = tmp_path / "eval_ref.yaml"
        eval_ref.write_text(f"summary_path: {summary_path}\n", encoding="utf-8")
        case_dir = tmp_path / "case_results" / "case_001"
        case_dir.mkdir(parents=True)
        (case_dir / "result.json").write_text(
            json.dumps(
                {
                    "case_id": "case_001",
                    "status": "passed",
                    "score": 0.0,
                    "evaluation": {"passed": False, "reason": "failed"},
                }
            ),
            encoding="utf-8",
        )
        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml")
        )

        async def fake_retrieve_experience(invocation: Any) -> dict[str, Any]:
            return {}

        async def fake_per_case(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"case_id": "case_001", "issue_category": "member_harness"}]

        async def fake_aggregate(*args: Any, **kwargs: Any) -> list[Any]:
            raise RuntimeError("aggregation failed")

        monkeypatch.setattr(strategy, "_retrieve_experience", fake_retrieve_experience)
        monkeypatch.setattr(strategy, "_per_case_diagnosis", fake_per_case)
        monkeypatch.setattr(strategy, "_aggregate_diagnosis", fake_aggregate)

        with pytest.raises(RuntimeError, match="aggregation failed"):
            await strategy.analyze(
                EvaluationResultAnalysisInvocation(
                    eval_ref_path=str(eval_ref),
                    case_results_dir=str(tmp_path / "case_results"),
                    case_traces_dir=str(tmp_path / "case_results"),
                    team_skill_ref_path="",
                    harness_refs_path="",
                    output_dir=str(tmp_path / "analysis"),
                )
            )

    @pytest.mark.asyncio
    async def test_run_agent_retries_valid_json_with_mojibake(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.core.runner import Runner
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

        attempts = 0

        async def fake_run_agent(*args: Any, **kwargs: Any) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return '{"issues": [{"summary": "\u7487\u8702\u8d1f\u93c2\u62cc\u5165"}]}'
            return '{"issues": []}'

        monkeypatch.setattr(Runner, "run_agent", fake_run_agent)

        raw = await analyzer_module._run_agent(object(), "prompt", max_retries=1)

        assert raw == '{"issues": []}'
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_run_agent_retries_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.core.runner import Runner
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

        attempts = 0

        async def fake_run_agent(*args: Any, **kwargs: Any) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise asyncio.TimeoutError("diagnosis model request timed out")
            return '{"issues": []}'

        monkeypatch.setattr(Runner, "run_agent", fake_run_agent)

        raw = await analyzer_module._run_agent(object(), "prompt", max_retries=1)

        assert raw == '{"issues": []}'
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_run_agent_repairs_non_json_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.core.runner import Runner
        from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

        prompts: list[str] = []

        async def fake_run_agent(*args: Any, **kwargs: Any) -> str:
            prompts.append(kwargs["inputs"]["query"])
            if len(prompts) == 1:
                return "Now let me analyze the evidence carefully. The case mostly passed."
            return '{"issue_category": "unassigned", "target_ref": "unassigned"}'

        monkeypatch.setattr(Runner, "run_agent", fake_run_agent)

        raw = await analyzer_module._run_agent(object(), "original diagnosis prompt", max_retries=1)

        assert json.loads(raw)["target_ref"] == "unassigned"
        assert len(prompts) == 2
        assert prompts[0] == "original diagnosis prompt"
        assert "Previous diagnosis output was not valid JSON" in prompts[1]
        assert "single valid JSON object" in prompts[1]


class TestDiagnosisPromptEvidenceSummary:
    """Verify per-case diagnosis prompt consumes bounded evidence, not raw case dirs."""

    def _make_case_input(self, result_path: str) -> Any:
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
        )

        return CaseAnalysisInput(
            case_id="case_001",
            status="passed",
            score=0.42,
            input="solve 1 + 1",
            expected=None,
            response="3",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=False,
            evaluation_reason="b1 not satisfied",
            evaluation_metadata={},
            trace_path=str(Path(result_path).parent / "trace.json"),
            result_path=result_path,
        )

    def _make_signals(self) -> Any:
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            DeterministicSignals,
        )

        return DeterministicSignals(method="llm_as_judge")

    def test_prompt_contains_evidence_summary_when_available(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_prompt,
        )

        result_path = tmp_path / "case_001" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")

        prompt = _build_diagnosis_prompt(
            case=self._make_case_input(str(result_path)),
            signals=self._make_signals(),
            retrieved_experience=None,
            evidence_summary_available=True,
            source_stage="member_stage",
        )

        assert "evidence_summary.md" in prompt
        assert "evidence_summary_text" in prompt
        assert "Use primary_evidence.evidence_summary_text" in prompt
        assert "If repository/ exists" in prompt
        assert "bounded read-only discriminator" in prompt
        assert "Authoritative Task Contract" in prompt
        assert "Commands and probes in Agent-Generated Execution Evidence are not task facts" in prompt
        assert '"evidence_summary_available": true' in prompt
        assert "Analyze concrete member harness capability" in prompt
        assert "member_harness.<role>.<variable>" in prompt
        assert "judge/normalized_trace.json" not in prompt
        assert "artifacts" not in prompt

    def test_prompt_uses_inline_json_when_summary_is_missing(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_prompt,
        )

        result_path = tmp_path / "case_002" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")

        prompt = _build_diagnosis_prompt(
            case=self._make_case_input(str(result_path)),
            signals=self._make_signals(),
            retrieved_experience=None,
            evidence_summary_available=False,
            source_stage="team_skill_stage",
        )

        assert "No evidence_summary.md is available" in prompt
        assert '"evidence_summary_available": false' in prompt
        assert "Analyze team organization" in prompt

    def test_prompt_does_not_contain_absolute_case_dir_path(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_prompt,
        )

        result_path = tmp_path / "case_003" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")

        prompt = _build_diagnosis_prompt(
            case=self._make_case_input(str(result_path)),
            signals=self._make_signals(),
            retrieved_experience=None,
            evidence_summary_available=True,
            source_stage="member_stage",
        )

        # Absolute case_dir path must NOT appear — only workspace-relative paths allowed
        assert str(tmp_path / "case_003") not in prompt
        assert str(result_path) not in prompt

    def test_diagnosis_input_json_evidence_block_uses_relative_paths(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )

        result_path = tmp_path / "case_004" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")

        raw = _build_diagnosis_input_json(
            case=self._make_case_input(str(result_path)),
            signals=self._make_signals(),
            retrieved_experience=None,
            evidence_summary_available=True,
        )
        payload = json.loads(raw)

        assert payload["authoritative_task_contract"]["provenance"] == "case.input"
        assert payload["authoritative_task_contract"]["input_excerpt"] == "solve 1 + 1"
        assert "not task-contract facts" in payload["authoritative_task_contract"]["policy"]
        assert payload["primary_evidence"]["evidence_summary_available"] is True
        assert payload["primary_evidence"]["evidence_summary_path"] == "evidence_summary.md"
        assert "Analyzer Evidence Summary" in payload["primary_evidence"]["evidence_summary_text"]
        assert "evidence" not in payload

    def test_diagnosis_input_includes_paired_candidate_feedback(
        self,
        tmp_path: Path,
    ) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )

        result_path = tmp_path / "case_feedback" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")

        payload = json.loads(
            _build_diagnosis_input_json(
                case=self._make_case_input(str(result_path)),
                signals=self._make_signals(),
                retrieved_experience=None,
                evidence_summary_available=True,
                prior_candidate_feedback={
                    "case_id": "case_001",
                    "experiments": [
                        {
                            "verifier_delta": {
                                "newly_passed_fail_to_pass": ["state_a"],
                                "remaining_failed_fail_to_pass": ["state_b"],
                            },
                        }
                    ],
                },
            )
        )

        assert payload["prior_candidate_feedback"]["experiments"][0]["verifier_delta"][
            "remaining_failed_fail_to_pass"
        ] == ["state_b"]
        assert "Preserve newly passing operations" in payload["prior_candidate_feedback_policy"]

    def test_diagnosis_input_preserves_complete_authoritative_task(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )

        result_path = tmp_path / "case_long" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        case = self._make_case_input(str(result_path))
        decisive_tail = "The nested field inherits datetimeformat from the root Schema owner."
        case = replace(case, input=("setup " * 300) + decisive_tail)

        payload = json.loads(
            _build_diagnosis_input_json(
                case=case,
                signals=self._make_signals(),
                retrieved_experience=None,
                evidence_summary_available=True,
            )
        )

        assert payload["authoritative_task_contract"]["input_excerpt"].endswith(decisive_tail)
        assert "[truncated" not in payload["authoritative_task_contract"]["input_excerpt"]

    def test_diagnosis_prompt_includes_experience_usage_policy(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )

        result_path = tmp_path / "case_005" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")

        raw = _build_diagnosis_input_json(
            case=self._make_case_input(str(result_path)),
            signals=self._make_signals(),
            retrieved_experience={
                "stage": "evaluation_result_analysis",
                "matches": [
                    {
                        "experience_id": "member_harness_experience_001",
                        "component_layer": "prompt_section",
                        "failure_signature": "verifier_loop_missing",
                        "summary": "Use verifier evidence before stopping.",
                    }
                ],
                "metadata": {"retrieval_status": "ok"},
            },
            evidence_summary_available=True,
        )
        payload = json.loads(raw)

        assert payload["retrieved_experience"]["matches"][0]["component_layer"] == "prompt_section"
        assert payload["experience_usage_policy"]["must_use_current_evidence_first"] is True
        assert "Do not copy a historical target_ref" in payload["experience_usage_policy"]["rules"][0]

    def test_system_prompt_requires_isolated_repository_investigation(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            DIAGNOSIS_SYSTEM_PROMPT,
        )

        assert "trace.json" in DIAGNOSIS_SYSTEM_PROMPT
        assert "result.json" in DIAGNOSIS_SYSTEM_PROMPT
        assert "repository/" in DIAGNOSIS_SYSTEM_PROMPT
        assert "at least two plausible mechanisms" in DIAGNOSIS_SYSTEM_PROMPT
        assert "repository-grounded discriminator" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Do NOT read case-root" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Never inspect benchmark gold/solution patches" in DIAGNOSIS_SYSTEM_PROMPT
        assert "authoritative_benchmark_test_contract.test_patch" in DIAGNOSIS_SYSTEM_PROMPT
        assert "acceptance evidence" in DIAGNOSIS_SYSTEM_PROMPT
        assert "hidden tests, `test_patch`" not in DIAGNOSIS_SYSTEM_PROMPT
        assert "evidence_summary.md" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Repository Investigation Protocol" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Experience Use Protocol" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Falsify the proposed mechanism" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Epistemic boundary" in DIAGNOSIS_SYSTEM_PROMPT
        assert "no-exception smoke probe" in DIAGNOSIS_SYSTEM_PROMPT
        assert "intermediate container" in DIAGNOSIS_SYSTEM_PROMPT
        assert "positive override case" in DIAGNOSIS_SYSTEM_PROMPT
        assert "authoritative_task_contract.input_excerpt" in DIAGNOSIS_SYSTEM_PROMPT
        assert "agent-generated command tested it" in DIAGNOSIS_SYSTEM_PROMPT

    def test_system_prompt_preserves_role_aware_target_refs(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            DIAGNOSIS_SYSTEM_PROMPT,
        )

        assert "member_harness.<role>.<variable>" in DIAGNOSIS_SYSTEM_PROMPT
        assert "team_skill.<role>.<variable>" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Never output role-less target_ref" in DIAGNOSIS_SYSTEM_PROMPT

    def test_system_prompt_no_longer_allows_rail_attribution(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            AGGREGATION_SYSTEM_PROMPT,
            DIAGNOSIS_SYSTEM_PROMPT,
        )

        assert "rail:" not in DIAGNOSIS_SYSTEM_PROMPT
        assert "Valid member_harness variables: prompt, skill, tool, config." in (AGGREGATION_SYSTEM_PROMPT)
        assert "Valid member_harness variables: prompt, skill, tool, rail, config." not in (AGGREGATION_SYSTEM_PROMPT)

    def test_prepare_evidence_summary_does_not_require_artifacts_dir(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _prepare_diagnosis_evidence,
        )

        case_dir = tmp_path / "case_results" / "fix-git"
        judge_dir = case_dir / "judge"
        verifier_dir = case_dir / "verifier"
        judge_dir.mkdir(parents=True)
        verifier_dir.mkdir()
        result_path = case_dir / "result.json"
        result_path.write_text("{}", encoding="utf-8")
        (judge_dir / "normalized_trace.json").write_text(
            json.dumps(
                {
                    "case_id": "fix-git",
                    "traces": [
                        {
                            "trace_id": "fix-git__solver__case",
                            "member_role": "solver",
                            "messages": [
                                {
                                    "role": "assistant",
                                    "message_index": 3,
                                    "tool_calls": [
                                        {
                                            "name": "shell",
                                            "input": "git status",
                                            "output": "",
                                            "error": "fatal: not a git repository",
                                            "step_pointer": "step_4",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (verifier_dir / "reward.txt").write_text("0\n", encoding="utf-8")
        (verifier_dir / "stderr.log").write_text("AssertionError: about.md is wrong\n", encoding="utf-8")
        runtime_dir = tmp_path / "runtime"

        available = _prepare_diagnosis_evidence(
            case=self._make_case_input(str(result_path)),
            runtime_dir=runtime_dir,
        )

        assert available is True
        summary = (runtime_dir / "evidence_summary.md").read_text(encoding="utf-8")
        assert "fix-git" in summary
        assert "reward=0" in summary
        assert "AssertionError" in summary
        assert "fatal: not a git repository" in summary
        assert not (runtime_dir / "artifacts").exists()

    def test_evidence_summary_includes_role_content_events(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _prepare_diagnosis_evidence,
        )

        case_dir = tmp_path / "case_results" / "webpage-case"
        judge_dir = case_dir / "judge"
        judge_dir.mkdir(parents=True)
        result_path = case_dir / "result.json"
        result_path.write_text("{}", encoding="utf-8")
        (judge_dir / "normalized_trace.json").write_text(
            json.dumps(
                {
                    "case_id": "webpage-case",
                    "traces": [
                        {
                            "trace_id": "webpage-case__content-writer__trajectory",
                            "member_role": "content-writer",
                            "messages": [
                                {
                                    "role": "assistant",
                                    "message_index": 0,
                                    "content": "I will write index.html now.",
                                    "step_pointer": "trajectory_step_1",
                                }
                            ],
                        },
                        {
                            "trace_id": "webpage-case__team_leader__trajectory",
                            "member_role": "team_leader",
                            "messages": [
                                {
                                    "role": "assistant",
                                    "message_index": 0,
                                    "content": (
                                        "Declared index.html and styles.css complete without observed file writes."
                                    ),
                                    "step_pointer": "trajectory_tail",
                                }
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        runtime_dir = tmp_path / "runtime"

        available = _prepare_diagnosis_evidence(
            case=self._make_case_input(str(result_path)),
            runtime_dir=runtime_dir,
        )

        assert available is True
        summary = (runtime_dir / "evidence_summary.md").read_text(encoding="utf-8")
        assert "role=content-writer" in summary
        assert "I will write index.html now." in summary
        assert "role=team_leader" in summary
        assert "Declared index.html" in summary

    def test_evidence_summary_preserves_project_suite_pass_verifier_fail(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
            _prepare_diagnosis_evidence,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            DeterministicSignals,
        )

        case_dir = tmp_path / "case_results" / "suite-contradiction"
        judge_dir = case_dir / "judge"
        judge_dir.mkdir(parents=True)
        result_path = case_dir / "result.json"
        long_test_output = "collected project tests\n" + ("." * 700) + "\n911 passed, 1 warning in 5.47s"
        result_path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "execution": {
                            "command_log": [
                                {
                                    "command": ("cd /testbed && python -m pytest tests/ -x -q"),
                                    "exit_code": 0,
                                    "stdout_excerpt": long_test_output,
                                    "stderr_excerpt": "",
                                }
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (judge_dir / "normalized_trace.json").write_text(
            json.dumps(
                {
                    "case_id": "suite-contradiction",
                    "traces": [
                        {
                            "trace_id": "suite-contradiction__solver__case",
                            "member_role": "solver",
                            "messages": [
                                {
                                    "message_index": 9,
                                    "tool_calls": [
                                        {
                                            "name": "bash",
                                            "input": ("cd /testbed && python -m pytest tests/ -x -q"),
                                            "output": long_test_output,
                                            "error": "",
                                            "step_pointer": "step_10",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        case = replace(
            self._make_case_input(str(result_path)),
            evaluation_passed=False,
        )
        runtime_dir = tmp_path / "runtime"

        assert _prepare_diagnosis_evidence(case=case, runtime_dir=runtime_dir) is True
        summary = (runtime_dir / "evidence_summary.md").read_text(encoding="utf-8")
        assert "project_test_suite_attempted: true" in summary
        assert "project_test_suite_result: passed" in summary
        assert "911 passed" in summary
        assert "do not diagnose skipped project testing" in summary

        payload = json.loads(
            _build_diagnosis_input_json(
                case=case,
                signals=DeterministicSignals(method="swebench"),
                retrieved_experience=None,
                evidence_summary_available=True,
            )
        )
        inventory = payload["deterministic_validation_inventory"]
        assert inventory["project_test_suite_attempted"] is True
        assert inventory["project_test_suite_result"] == "passed"
        assert inventory["authoritative_verifier_result"] == "failed"

    def test_diagnosis_gate_rejects_skipped_suite_claim_after_suite_pass(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        inventory = {
            "project_test_suite_attempted": True,
            "project_test_suite_result": "passed",
            "authoritative_verifier_result": "failed",
        }
        diagnosis = {
            "recommendation": (
                "Require running the project's existing test suite rather than only a self-authored smoke probe."
            ),
            "validation_observations": {
                "project_test_suite_attempted": True,
                "project_test_suite_result": "passed",
                "authoritative_verifier_result": "failed",
                "contradiction_explanation": "The verifier checks a hidden semantic contract.",
            },
        }

        conflicts = _diagnosis_validation_conflicts(diagnosis, inventory)

        assert conflicts == ["recommendation contradicts observed successful project-suite execution"]

    def test_diagnosis_gate_accepts_semantic_contract_explanation(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        inventory = {
            "project_test_suite_attempted": True,
            "project_test_suite_result": "passed",
            "authoritative_verifier_result": "failed",
        }
        diagnosis = {
            "recommendation": (
                "Add a causal discriminator for inherited owner semantics and a targeted contract probe before editing."
            ),
            "validation_observations": {
                "project_test_suite_attempted": True,
                "project_test_suite_result": "passed",
                "authoritative_verifier_result": "failed",
                "contradiction_explanation": ("The local suite lacks the verifier's inherited-format assertion."),
            },
        }

        assert _diagnosis_validation_conflicts(diagnosis, inventory) == []

    def test_diagnosis_gate_does_not_guess_semantics_from_keywords(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "summary": "A nested field was bound through an intermediate container.",
            "root_cause": "The container has no local opts attribute.",
            "critical_mistake": "The solver read config from the parent field.",
            "recommendation": "Use DEFAULT_FORMAT when opts is absent.",
        }

        assert _diagnosis_validation_conflicts(diagnosis, {}) == []

    def test_diagnosis_gate_rejects_patch_apply_claim_when_patch_applied(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "summary": ("Untracked files polluted the working tree, causing the SWE-bench patch application to fail."),
            "verifier_observations": {
                "patch_successfully_applied": True,
                "failed_fail_to_pass_tests": ["test_next"],
                "failed_pass_to_pass_tests": [],
            },
        }
        verifier_inventory = {
            "patch_successfully_applied": True,
            "resolved": False,
            "failed_fail_to_pass_tests": ["test_next"],
            "failed_pass_to_pass_tests": [],
        }

        conflicts = _diagnosis_validation_conflicts(
            diagnosis,
            {},
            verifier_inventory,
        )

        assert conflicts == ["diagnosis contradicts authoritative successful patch application"]

    def test_diagnosis_gate_accepts_failed_authoritative_test_attribution(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "summary": (
                "The patch omitted direct __next__ state initialization and the "
                "pre-initialization AttributeError boundary required by test_next."
            ),
            "verifier_observations": {
                "patch_successfully_applied": True,
                "failed_fail_to_pass_tests": ["test_next"],
                "failed_pass_to_pass_tests": [],
            },
        }
        verifier_inventory = {
            "patch_successfully_applied": True,
            "resolved": False,
            "failed_fail_to_pass_tests": ["test_next"],
            "failed_pass_to_pass_tests": [],
            "verifier_failure_output_excerpt": ("FAILED test_next: AttributeError before direct next is initialized"),
        }

        assert (
            _diagnosis_validation_conflicts(
                diagnosis,
                {},
                verifier_inventory,
            )
            == []
        )

    def test_diagnosis_gate_rejects_generic_iterable_attribution_for_test_next(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "summary": "The object did not fully implement the iterable protocol.",
            "recommendation": "Add __iter__ and verify that list(obj) returns values.",
            "verifier_observations": {
                "patch_successfully_applied": True,
                "failed_fail_to_pass_tests": ["test_next"],
                "failed_pass_to_pass_tests": [],
            },
        }
        verifier_inventory = {
            "patch_successfully_applied": True,
            "resolved": False,
            "failed_fail_to_pass_tests": ["test_next"],
            "failed_pass_to_pass_tests": [],
            "verifier_failure_output_excerpt": ("FAILED test_next: AttributeError before direct next is initialized"),
        }

        assert _diagnosis_validation_conflicts(
            diagnosis,
            {},
            verifier_inventory,
        ) == [
            "test_next attribution must preserve the direct operation and its stateful iterator lifecycle",
            "test_next attribution omits the verifier's pre-initialization boundary",
        ]

    def test_diagnosis_gate_rejects_instruction_for_known_edit_after_empty_patch(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "summary": "The solver correctly diagnosed the defect but emitted no patch.",
            "root_cause": "The solver completed investigation and then returned.",
            "target_ref": "member_harness.solver.skill",
            "decision_contract": {
                "required_action": "Write a concrete edit to the identified source file.",
                "activation_phase": "during_investigation",
            },
        }

        assert _diagnosis_validation_conflicts(
            diagnosis,
            {},
            {"empty_patch": True},
        ) == [
            "empty-patch diagnosis says the concrete edit was already justified, "
            "so activation_phase must be post_diagnosis or pre_submission",
            "empty-patch diagnosis attributes a post-diagnosis action transition to a reusable harness instruction",
        ]

    def test_diagnosis_gate_keeps_earlier_investigation_error_actionable(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "summary": "The solver never found the implementation owner.",
            "root_cause": "It guessed unavailable APIs instead of reading source.",
            "target_ref": "member_harness.solver.skill",
            "decision_contract": {
                "required_action": (
                    "After the first unavailable API error, inspect the owning source "
                    "and run a discriminator before designing an edit."
                ),
                "activation_phase": "during_investigation",
            },
        }

        assert (
            _diagnosis_validation_conflicts(
                diagnosis,
                {},
                {"empty_patch": True},
            )
            == []
        )

    def test_diagnosis_gate_rejects_late_empty_patch_instruction_without_phrase_match(
        self,
    ) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "summary": "The final turn contained no workspace action.",
            "target_ref": "member_harness.solver.prompt_section",
            "decision_contract": {
                "required_action": "Persist the selected correction.",
                "activation_phase": "post_diagnosis",
            },
        }

        assert _diagnosis_validation_conflicts(
            diagnosis,
            {},
            {"empty_patch": True},
        ) == [
            "empty-patch post-diagnosis action transition must be "
            "target_ref=unassigned rather than reusable Prompt/Skill",
        ]

    def test_diagnosis_gate_rejects_encoding_only_safe_replace_attribution(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "target_ref": "member_harness.solver.skill",
            "decision_contract": {
                "causal_distinction": "A lossy error handler changes Unicode text.",
                "required_action": "Always write with UTF-8 and no error handler.",
                "acceptance_observable": "Unicode content round-trips.",
                "scope_boundary": ["Ignoring characters is also lossy."],
            },
        }

        assert _diagnosis_validation_conflicts(
            diagnosis,
            {},
            {
                "failed_fail_to_pass_tests": [
                    "test_safe_create_replace_file[utf8_update]",
                ],
                "verifier_failure_output_excerpt": "existing file must survive",
            },
        ) == [
            "safe file-replacement attribution must preserve the transactional "
            "boundary: write separately and leave the existing file unchanged "
            "when encoding or writing fails",
        ]

    def test_diagnosis_gate_accepts_transactional_safe_replace_attribution(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        diagnosis = {
            "target_ref": "member_harness.solver.skill",
            "decision_contract": {
                "causal_distinction": "Encoding failure must not damage an existing file.",
                "required_action": (
                    "Write a temporary file and atomically replace the destination only after the write succeeds."
                ),
                "acceptance_observable": "The original file is unchanged on failure.",
                "scope_boundary": ["Changing the encoding alone is insufficient."],
            },
        }

        assert (
            _diagnosis_validation_conflicts(
                diagnosis,
                {},
                {
                    "failed_fail_to_pass_tests": [
                        "test_safe_create_replace_file[utf8_update]",
                    ],
                },
            )
            == []
        )

    def test_verifier_inventory_preserves_authoritative_failure_output(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_evidence_summary,
            _build_verifier_inventory,
        )

        failure_output = "FAILED test_next\nE AttributeError: iterator state is not initialized before direct next"
        case = _case_input(
            case_id="pydicom__pydicom-1139",
            method="swebench_official",
            status="failed",
            passed=False,
            metadata={
                "test_output_excerpt": failure_output,
                "instance_report": {
                    "pydicom__pydicom-1139": {
                        "patch_successfully_applied": True,
                        "resolved": False,
                        "tests_status": {
                            "FAIL_TO_PASS": {"failure": ["test_next"]},
                            "PASS_TO_PASS": {"failure": []},
                        },
                    }
                },
            },
        )

        inventory = _build_verifier_inventory(case)

        assert inventory["verifier_failure_output_excerpt"] == failure_output
        summary = _build_evidence_summary(case)
        assert "Authoritative failure output excerpt" in summary
        assert "iterator state is not initialized" in summary

    def test_empty_patch_inventory_is_preserved_without_instance_report(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_evidence_summary,
            _build_verifier_inventory,
        )

        case = _case_input(
            case_id="astroid__astroid-1333",
            method="swebench_official",
            status="failed",
            passed=False,
            metadata={"empty_patch": True, "instance_report": {}},
        )

        assert _build_verifier_inventory(case) == {"empty_patch": True}
        assert "empty_patch: true" in _build_evidence_summary(case)

    def test_aggregation_prompt_includes_retrieved_experience(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_aggregation_prompt,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            DeterministicSignals,
            EvaluationSummaryInput,
        )

        prompt = _build_aggregation_prompt(
            summary=EvaluationSummaryInput(total_cases=1, failed_count=1),
            per_case_diagnoses=[
                {
                    "case_id": "case_001",
                    "root_cause": "stopped before verifier repair",
                    "target_ref": "member_harness.prompt",
                    "confidence": "medium",
                }
            ],
            signals=DeterministicSignals(method="terminal_bench"),
            retrieved_experience={
                "stage": "evaluation_result_analysis",
                "matches": [
                    {
                        "experience_id": "member_harness_experience_001",
                        "component_layer": "prompt_section",
                        "summary": "Verifier failures should trigger inspect-repair-reverify.",
                    }
                ],
                "metadata": {"retrieval_status": "ok"},
            },
            max_issues=3,
            evidence_limit_per_issue=2,
            source_stage="member_stage",
        )

        assert "Retrieved Experience" in prompt
        assert "member_harness_experience_001" in prompt
        assert "Current evidence remains authoritative" in prompt
        assert "Analyze concrete member harness capability" in prompt


class TestJudgeBreakdown:
    """Contracts for _summarize_evaluation_metadata and judge_breakdown in diagnosis input."""

    def test_summarize_evaluation_metadata_extracts_behaviors(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        metadata = {
            "parsed": {
                "behaviors": [
                    {"id": "b1", "score": 0.0, "reason": "output was wrong", "evidence": "step_3"},
                    {"id": "b2", "score": 1.0, "reason": "correct", "evidence": ""},
                ],
                "overall_reason": "b1 failed due to arithmetic error",
                "forbidden_hits": [],
            }
        }

        result = _summarize_evaluation_metadata(metadata)

        assert len(result["behaviors"]) == 2
        assert result["behaviors"][0]["id"] == "b1"
        assert result["behaviors"][0]["score"] == 0.0
        assert "output was wrong" in result["behaviors"][0]["reason"]
        assert result["behaviors"][1]["id"] == "b2"
        assert "arithmetic error" in result["overall_reason"]
        assert result["forbidden_hits"] == []

    def test_summarize_evaluation_metadata_extracts_behavior_diagnostics(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        metadata = {
            "parsed": {
                "behaviors": [
                    {
                        "id": "artifact_validation",
                        "score": 0.2,
                        "reason": "deck artifact was missing",
                        "failure_reason": "No inspectable PPT or artifact-equivalent output was produced.",
                        "missing_capability": "deterministic artifact validation",
                        "suggested_surface_hint": "tool",
                        "evidence": "artifacts/",
                    }
                ],
                "overall_reason": "artifact validation failed",
            }
        }

        result = _summarize_evaluation_metadata(metadata)

        behavior = result["behaviors"][0]
        assert behavior["failure_reason"].startswith("No inspectable")
        assert behavior["missing_capability"] == "deterministic artifact validation"
        assert behavior["suggested_surface_hint"] == "tool"

    def test_summarize_evaluation_metadata_extracts_quality_gaps(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        metadata = {
            "parsed": {
                "behaviors": [
                    {
                        "id": "playable_loop",
                        "score": 0.0,
                        "reason": "no event handlers",
                        "failure_reason": "controls do nothing",
                        "missing_capability": "interactive state loop",
                        "suggested_surface_hint": "tool",
                        "evidence": "artifacts/game.js",
                    }
                ],
                "overall_reason": "game is not playable",
                "quality_gaps": [
                    {
                        "id": "missing_interaction_binding",
                        "gap_type": "artifact_quality_gap",
                        "dimension": "runtime correctness",
                        "severity": "high",
                        "affected_roles": ["frontend-engineer"],
                        "likely_surfaces": ["tool"],
                        "evidence": "event_handler_count=0",
                        "missing_capability": "wire DOM controls to state transitions",
                        "why_it_matters": "buttons exist but no action can happen",
                        "data_needed_to_fix": "cases requiring DOM event binding",
                    }
                ],
                "quality_gap_score_ceiling": 0.1,
                "dataset_budget": {
                    "total_cases": 3,
                    "case_groups": [
                        {
                            "source_gap": "missing_interaction_binding",
                            "target_roles": ["frontend-engineer"],
                            "target_surfaces": ["tool"],
                        }
                    ],
                },
            }
        }

        result = _summarize_evaluation_metadata(metadata)

        assert result["quality_gaps"][0]["id"] == "missing_interaction_binding"
        assert result["quality_gaps"][0]["affected_roles"] == ["frontend-engineer"]
        assert result["quality_gaps"][0]["likely_surfaces"] == ["tool"]
        assert result["quality_gaps"][0]["missing_capability"] == "wire DOM controls to state transitions"
        assert result["quality_gap_score_ceiling"] == 0.1
        assert result["dataset_budget"]["case_groups"][0]["source_gap"] == "missing_interaction_binding"

    def test_summarize_evaluation_metadata_excludes_verification_gaps_from_optimizer(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        metadata = {
            "parsed": {
                "behaviors": [
                    {
                        "id": "runtime_behavior",
                        "score": 0.9,
                        "reason": "artifact behavior passed",
                    }
                ],
                "quality_gaps": [
                    {
                        "id": "limited_smoke_scope",
                        "gap_type": "verification_gap",
                        "severity": "low",
                        "affected_roles": ["frontend-developer"],
                        "likely_surfaces": ["tool"],
                        "evidence": "interaction_smoke probes initial controls only",
                        "missing_capability": "full evaluator workflow probe",
                    }
                ],
            }
        }

        result = _summarize_evaluation_metadata(metadata)

        assert "quality_gaps" not in result
        assert result["behaviors"][0]["id"] == "runtime_behavior"

    def test_summarize_evaluation_metadata_returns_empty_when_no_behaviors(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        metadata: dict[str, Any] = {"parsed": {"dimensions": {"avg_behavior_score": 0.5}}}

        result = _summarize_evaluation_metadata(metadata)

        assert result == {}

    def test_summarize_evaluation_metadata_returns_empty_for_non_judge_cases(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        assert _summarize_evaluation_metadata({}) == {}
        assert _summarize_evaluation_metadata({"attempt": 1}) == {}

    def test_build_diagnosis_input_json_contains_judge_breakdown_for_llm_judge(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        result_path = tmp_path / "case_bd" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_bd",
            status="failed",
            score=0.3,
            input="query",
            expected=None,
            response="wrong",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=False,
            evaluation_reason="b1 failed",
            evaluation_metadata={
                "parsed": {
                    "behaviors": [
                        {
                            "id": "b1",
                            "score": 0.0,
                            "reason": "missing required output",
                            "failure_reason": "did not create the required deck artifact",
                            "missing_capability": "artifact completeness validation",
                            "suggested_surface_hint": "tool",
                            "evidence": "step_2",
                        },
                    ],
                    "overall_reason": "critical behavior b1 not satisfied",
                    "forbidden_hits": ["forbidden_phrase"],
                }
            },
            trace_path=str(result_path.parent / "trace.json"),
            result_path=str(result_path),
        )

        raw = _build_diagnosis_input_json(
            case=case,
            signals=DeterministicSignals(method="llm_as_judge"),
            retrieved_experience=None,
            evidence_summary_available=False,
        )
        payload = json.loads(raw)

        breakdown = payload["case_facts"]["judge_breakdown"]
        assert len(breakdown["behaviors"]) == 1
        assert breakdown["behaviors"][0]["id"] == "b1"
        assert "missing required output" in breakdown["behaviors"][0]["reason"]
        assert breakdown["behaviors"][0]["missing_capability"] == "artifact completeness validation"
        assert breakdown["behaviors"][0]["suggested_surface_hint"] == "tool"
        assert "critical behavior" in breakdown["overall_reason"]
        assert breakdown["forbidden_hits"] == ["forbidden_phrase"]

    def test_build_diagnosis_input_json_contains_quality_gaps_for_llm_judge(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        result_path = tmp_path / "case_gap" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_gap",
            status="failed",
            score=0.1,
            input="query",
            expected=None,
            response="wrong",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=False,
            evaluation_reason="game controls do nothing",
            evaluation_metadata={
                "parsed": {
                    "behaviors": [
                        {
                            "id": "playable_loop",
                            "score": 0.0,
                            "reason": "controls do nothing",
                            "failure_reason": "no handlers",
                            "missing_capability": "DOM interaction binding",
                            "suggested_surface_hint": "tool",
                            "evidence": "artifacts/game.js",
                        }
                    ],
                    "quality_gaps": [
                        {
                            "id": "missing_interaction_binding",
                            "severity": "high",
                            "affected_roles": ["frontend-engineer"],
                            "likely_surfaces": ["tool"],
                            "missing_capability": "DOM interaction binding",
                            "evidence": "event_handler_count=0",
                        }
                    ],
                }
            },
            trace_path=str(result_path.parent / "trace.json"),
            result_path=str(result_path),
        )

        raw = _build_diagnosis_input_json(
            case=case,
            signals=DeterministicSignals(method="llm_as_judge"),
            retrieved_experience=None,
            evidence_summary_available=False,
        )
        payload = json.loads(raw)

        gaps = payload["case_facts"]["judge_breakdown"]["quality_gaps"]
        assert gaps[0]["id"] == "missing_interaction_binding"
        assert gaps[0]["affected_roles"] == ["frontend-engineer"]
        assert gaps[0]["likely_surfaces"] == ["tool"]

    def test_build_evidence_summary_contains_quality_gaps(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_evidence_summary,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput

        case_dir = tmp_path / "case_summary"
        case_dir.mkdir(parents=True)
        result_path = case_dir / "result.json"
        result_path.write_text("{}", encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_summary",
            status="failed",
            score=0.1,
            input="query",
            expected=None,
            response="wrong",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=False,
            evaluation_reason="game controls do nothing",
            evaluation_metadata={
                "parsed": {
                    "behaviors": [],
                    "quality_gaps": [
                        {
                            "id": "missing_interaction_binding",
                            "severity": "high",
                            "affected_roles": ["frontend-engineer"],
                            "likely_surfaces": ["tool"],
                            "missing_capability": "DOM interaction binding",
                            "evidence": "event_handler_count=0",
                            "why_it_matters": "buttons exist but do nothing",
                        }
                    ],
                }
            },
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )

        summary = _build_evidence_summary(case)

        assert "## Judge Quality Gaps" in summary
        assert "missing_interaction_binding" in summary
        assert "frontend-engineer" in summary
        assert "tool" in summary

    def test_build_diagnosis_input_json_judge_breakdown_empty_for_non_judge(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        result_path = tmp_path / "case_nm" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_nm",
            status="failed",
            score=0.0,
            input="query",
            expected=None,
            response="wrong",
            error="AssertionError",
            evaluation_method="script_based",
            evaluation_passed=False,
            evaluation_reason="test failed",
            evaluation_metadata={},
            trace_path=str(result_path.parent / "trace.json"),
            result_path=str(result_path),
        )

        raw = _build_diagnosis_input_json(
            case=case,
            signals=DeterministicSignals(method="script_based"),
            retrieved_experience=None,
            evidence_summary_available=False,
        )
        payload = json.loads(raw)

        # non-llm_as_judge: judge_breakdown must be an empty dict, not raise
        assert payload["case_facts"]["judge_breakdown"] == {}


class TestAttributionMetadata:
    """Contracts for attribution flat→nested conversion and TeamIssue.metadata preservation."""

    def test_dict_to_team_issue_writes_attribution_from_nested_metadata(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_attr_001",
                "category": "member_harness",
                "severity": "high",
                "summary": "skill misconfigured",
                "affected_cases": ["case_001"],
                "evidence": [],
                "suspected_team_scope": "member",
                "recommendation": "fix skill config",
                "metadata": {
                    "attribution": {
                        "root_cause": "skill missing retry logic",
                        "critical_mistake": "first tool call at step 3",
                        "general_mechanism": "add retry guard",
                        "target_ref": "member_harness.skill",
                        "evidence_refs": [
                            {"trace_id": "t1", "role": "executor", "message_index": 3, "step_pointer": "step_3"}
                        ],
                        "confidence": "high",
                    }
                },
            }
        )

        attribution = issue.metadata["attribution"]
        assert attribution["target_ref"] == "member_harness.skill"
        assert attribution["confidence"] == "high"
        assert len(attribution["evidence_refs"]) == 1
        assert attribution["evidence_refs"][0]["role"] == "executor"

    def test_dict_to_team_issue_builds_attribution_from_flat_top_level_fields(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _dict_to_team_issue,
        )

        issue = _dict_to_team_issue(
            {
                "issue_id": "issue_attr_002",
                "category": "team_coordination",
                "severity": "medium",
                "summary": "handoff lost context",
                "affected_cases": ["case_002"],
                "evidence": [],
                "suspected_team_scope": "team_skill",
                "recommendation": "tighten handoff protocol",
                "root_cause": "context dropped at handoff",
                "critical_mistake": "coordinator did not forward result",
                "general_mechanism": "shared context contract check",
                "target_ref": "team_skill.handoff_protocol",
                "evidence_refs": [],
                "confidence": "medium",
            }
        )

        attribution = issue.metadata["attribution"]
        assert attribution["target_ref"] == "team_skill.handoff_protocol"
        assert attribution["root_cause"] == "context dropped at handoff"
        assert attribution["confidence"] == "medium"

    def test_compact_per_case_diagnoses_builds_attribution_sub_dict(self) -> None:
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            _compact_per_case_diagnoses,
        )

        per_case = [
            {
                "case_id": "case_001",
                "issue_category": "member_harness",
                "severity": "high",
                "summary": "tool failed",
                "failure_mode": "repeated_failed_tool_call",
                "root_cause": "tool endpoint unreachable",
                "critical_mistake": "step_3 tool call",
                "general_mechanism": "add fallback",
                "decision_contract": {
                    "wrong_decision": "retry the unreachable endpoint",
                    "causal_distinction": "endpoint failure is stable across retries",
                    "required_action": "switch to the available local fallback",
                    "acceptance_observable": "the local operation succeeds",
                    "scope_boundary": ["do not retry the same endpoint"],
                },
                "target_ref": "member_harness.tool",
                "evidence_refs": [{"trace_id": "t1", "role": "executor", "message_index": 3, "step_pointer": ""}],
                "affected_components": ["executor"],
                "recommendation": "fix tool config",
                "confidence": "high",
            }
        ]

        compact = _compact_per_case_diagnoses(per_case)

        assert len(compact) == 1
        entry = compact[0]
        assert "attribution" in entry
        assert entry["attribution"]["target_ref"] == "member_harness.tool"
        assert entry["attribution"]["confidence"] == "high"
        assert entry["attribution"]["decision_contract"]["required_action"] == (
            "switch to the available local fallback"
        )
        assert len(entry["attribution"]["evidence_refs"]) == 1
        # old flat fields must NOT remain at top level
        assert "decisive_step" not in entry
        assert "anchor_strength" not in entry
        assert "root_cause" not in entry


class TestDeterministicAggregation:
    @pytest.mark.asyncio
    async def test_aggregate_diagnosis_uses_structured_per_case_targets_without_agent(
        self,
        tmp_path: Path,
    ) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            DiagnosisAgentStrategy,
        )
        from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
            DeterministicSignals,
            EvaluationSummaryInput,
        )

        strategy = DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))

        async def fail_build_agent(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("aggregation should not require another LLM call")

        strategy._build_agent = fail_build_agent  # type: ignore[method-assign]

        issues = await strategy._aggregate_diagnosis(
            [
                {
                    "case_id": "case_a",
                    "issue_category": "team_skill",
                    "severity": "high",
                    "summary": "leader signed off without checking final trace",
                    "failure_mode": "false_signoff_constraint_bypass",
                    "root_cause": "team leader accepted a 4-turn trace for a 5-turn requirement",
                    "critical_mistake": "final signoff skipped the rubric count check",
                    "general_mechanism": "team final-gate must compare evidence against hard constraints",
                    "decision_contract": {
                        "wrong_decision": "sign off before counting completed turns",
                        "causal_distinction": "declared completion differs from observed count",
                        "required_action": "compare the observed count before signoff",
                        "acceptance_observable": "the observed count satisfies the requirement",
                        "scope_boundary": ["a completion claim is not equivalent to evidence"],
                    },
                    "target_ref": "team_skill.qa-integrator.constraint_violation",
                    "evidence_refs": [
                        {
                            "trace_id": "case_a__team__trace",
                            "role": "team_leader",
                            "message_index": 0,
                            "step_pointer": "step_1",
                        }
                    ],
                    "affected_components": ["qa-integrator", "team_leader"],
                    "recommendation": "tighten team_skill.qa-integrator.constraint_violation",
                    "confidence": "high",
                },
                {
                    "case_id": "case_b",
                    "issue_category": "unassigned",
                    "severity": "low",
                    "summary": "verifier could not run browser runtime checks",
                    "failure_mode": "verifier_methodology_score_ceiling",
                    "root_cause": "score gap belongs to verifier method",
                    "critical_mistake": "no team or member mistake",
                    "general_mechanism": "keep verifier gaps outside optimizer loop",
                    "target_ref": "unassigned",
                    "evidence_refs": [],
                    "affected_components": [],
                    "recommendation": "fix evaluator outside harness optimization",
                    "confidence": "low",
                },
            ],
            EvaluationSummaryInput(
                total_cases=2,
                passed_count=1,
                failed_count=1,
                average_score=0.79,
                evaluation_method="llm_as_judge",
            ),
            DeterministicSignals(method="llm_as_judge"),
            retrieved_experience=None,
            output_dir=tmp_path,
        )

        assert len(issues) == 1
        issue = issues[0]
        assert issue.category == "team_coordination"
        assert issue.optimization_target == "team_skill"
        assert issue.affected_cases == ["case_a"]
        assert issue.metadata["attribution"]["target_ref"] == ("team_skill.qa_integrator.constraint_violation")
        assert issue.metadata["attribution"]["decision_contract"]["required_action"] == (
            "compare the observed count before signoff"
        )


class TestAnalyzerFacadeArtifacts:
    """End-to-end facade behavior that does not require a real DeepAgent."""

    @pytest.mark.asyncio
    async def test_empty_case_results_writes_empty_analysis_artifact(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.schema import EvaluationResultAnalysisInvocation

        fake_experience_learner = _FakeExperienceLearner()
        analyzer = EvaluationResultAnalyzer(
            EvaluationResultAnalyzerConfig(output_filename="issues.yaml"),
            experience_learner=fake_experience_learner,
        )
        output_dir = tmp_path / "analysis"
        eval_ref_path = _write_empty_eval_ref(tmp_path)
        invocation = EvaluationResultAnalysisInvocation(
            eval_ref_path=str(eval_ref_path),
            case_results_dir=str(tmp_path / "empty_case_results"),
            case_traces_dir=str(tmp_path / "empty_case_results"),
            team_skill_ref_path=str(tmp_path / "team_skill.yaml"),
            harness_refs_path=str(tmp_path / "harness_refs.yaml"),
            output_dir=str(output_dir),
        )

        analysis_ref_path = await analyzer.analyze(invocation)

        analysis_ref = yaml.safe_load(Path(analysis_ref_path).read_text(encoding="utf-8"))
        issues_path = Path(analysis_ref["issues_path"])
        issues_payload = yaml.safe_load(issues_path.read_text(encoding="utf-8"))

        assert fake_experience_learner.calls == [
            {
                "stage": "evaluation_result_analysis",
                "eval_ref_path": str(eval_ref_path),
                "analysis_result_path": "",
                "harness_refs_path": str(tmp_path / "harness_refs.yaml"),
                "candidate_modules": ["team_skill", "member_harness"],
            }
        ]
        assert analysis_ref["metadata"]["analysis_status"] == "empty_case_results"
        assert analysis_ref["issues"] == []
        assert issues_payload == {"issues": []}
        assert issues_path.name == "issues.yaml"

    @pytest.mark.asyncio
    async def test_analysis_ref_backfills_case_evidence_refs(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.schema import EvaluationResultAnalysisInvocation

        case_dir = tmp_path / "case_results" / "case_001_abc"
        (case_dir / "judge").mkdir(parents=True)
        result_path = case_dir / "result.json"
        trace_path = case_dir / "trace.json"
        normalized_trace_path = case_dir / "judge" / "normalized_trace.json"
        _write_json(result_path, {"case_id": "case_001", "score": 0.0})
        _write_json(trace_path, {"case_id": "case_001", "events": []})
        _write_json(normalized_trace_path, {"case_id": "case_001", "traces": []})

        analyzer = EvaluationResultAnalyzer(EvaluationResultAnalyzerConfig(output_filename="issues.yaml"))
        analyzer._strategy = _FakeIssueStrategy()

        analysis_ref_path = await analyzer.analyze(
            EvaluationResultAnalysisInvocation(
                eval_ref_path=str(tmp_path / "eval_ref.yaml"),
                case_results_dir=str(tmp_path / "case_results"),
                case_traces_dir=str(tmp_path / "case_results"),
                team_skill_ref_path="team_skill",
                harness_refs_path="harness_refs.yaml",
                output_dir=str(tmp_path / "analysis"),
            )
        )

        analysis_ref = yaml.safe_load(Path(analysis_ref_path).read_text(encoding="utf-8"))
        attribution = analysis_ref["issues"][0]["metadata"]["attribution"]
        assert attribution["evidence_refs"] == [
            {
                "case_id": "case_001",
                "result_path": str(result_path.resolve()),
                "trace_path": str(trace_path.resolve()),
                "normalized_trace_path": str(normalized_trace_path.resolve()),
            }
        ]


class TestAnalyzerRealModelIntegration:
    """Real-model analyzer smoke test guarded by explicit local configuration."""

    @pytest.mark.asyncio
    async def test_real_model_runs_complete_analyzer_pipeline(self, tmp_path: Path) -> None:
        model_config_ref = os.environ.get("AUTO_COORDINATING_ANALYZER_MODEL_CONFIG_REF", "").strip()
        if not model_config_ref:
            pytest.skip("set AUTO_COORDINATING_ANALYZER_MODEL_CONFIG_REF to run real-model analyzer test")

        model_config_path = Path(model_config_ref).expanduser()
        if not model_config_path.is_file():
            pytest.skip("AUTO_COORDINATING_ANALYZER_MODEL_CONFIG_REF must point to an existing model YAML")

        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.schema import EvaluationResultAnalysisInvocation

        artifacts = _write_evaluation_artifacts(tmp_path, method="llm_as_judge")
        output_dir = tmp_path / "analysis"
        analyzer = EvaluationResultAnalyzer(
            EvaluationResultAnalyzerConfig(
                model_config_ref=str(model_config_path),
                max_issues=3,
                evidence_limit_per_issue=3,
                output_filename="team_issues.yaml",
            ),
            experience_learner=_FakeExperienceLearner(),
        )
        invocation = EvaluationResultAnalysisInvocation(
            eval_ref_path=str(artifacts["eval_ref_path"]),
            case_results_dir=str(artifacts["case_results_dir"]),
            case_traces_dir=str(artifacts["case_results_dir"]),
            team_skill_ref_path=str(tmp_path / "team_skill.yaml"),
            harness_refs_path=str(tmp_path / "harness_refs.yaml"),
            output_dir=str(output_dir),
        )

        analysis_ref_path = await analyzer.analyze(invocation)
        analysis_ref = yaml.safe_load(Path(analysis_ref_path).read_text(encoding="utf-8"))
        issues_path = Path(analysis_ref["issues_path"])
        issues_payload = yaml.safe_load(issues_path.read_text(encoding="utf-8"))

        assert Path(analysis_ref_path).is_file()
        assert issues_path.is_file()
        assert analysis_ref["metadata"]["analysis_status"] in {"completed", "partial"}
        assert analysis_ref["metadata"]["analysis_status"] != "mocked"
        assert isinstance(analysis_ref["issues"], list)
        assert len(analysis_ref["issues"]) >= 1
        assert issues_payload["issues"] == analysis_ref["issues"]
        assert analysis_ref["issues"][0]["category"] in {"member_harness", "team_coordination"}
        assert analysis_ref["issues"][0]["optimization_target"] in {"team_skill", "member_harness"}

    @pytest.mark.asyncio
    async def test_real_model_analyzes_evaluations_directory(self, tmp_path: Path) -> None:
        evaluations_dir_env = os.environ.get("AUTO_COORDINATING_ANALYZER_EVALUATIONS_DIR", "").strip()
        if not evaluations_dir_env:
            pytest.skip("set AUTO_COORDINATING_ANALYZER_EVALUATIONS_DIR to a real evaluations directory")
        evaluations_dir = Path(evaluations_dir_env).expanduser().resolve()
        if not evaluations_dir.is_dir():
            pytest.skip(f"AUTO_COORDINATING_ANALYZER_EVALUATIONS_DIR is not a directory: {evaluations_dir}")

        eval_ref_path = evaluations_dir / "eval_ref.yaml"
        if not eval_ref_path.is_file():
            pytest.skip(f"eval_ref.yaml not found in {evaluations_dir}")

        model_config_ref = os.environ.get("AUTO_COORDINATING_ANALYZER_MODEL_CONFIG_REF", "").strip()
        if not model_config_ref:
            pytest.skip("set AUTO_COORDINATING_ANALYZER_MODEL_CONFIG_REF to run real-model analyzer test")
        model_config_path = Path(model_config_ref).expanduser()
        if not model_config_path.is_file():
            pytest.skip("AUTO_COORDINATING_ANALYZER_MODEL_CONFIG_REF must point to an existing model YAML")

        eval_ref = yaml.safe_load(eval_ref_path.read_text(encoding="utf-8"))
        case_results_dir = _resolve_case_results_dir(eval_ref, evaluations_dir)
        if not case_results_dir.is_dir():
            pytest.skip(f"case_results directory not found: {case_results_dir}")

        case_result_files = sorted(case_results_dir.glob("*/result.json"))
        if not case_result_files:
            pytest.skip(f"no case result.json files found under {case_results_dir}")
        bounded_eval = _materialize_bounded_evaluation_dir(
            eval_ref=eval_ref,
            evaluations_dir=evaluations_dir,
            case_result_files=case_result_files,
            output_root=tmp_path,
            max_cases=_real_model_max_cases(),
        )
        all_real_case_ids = set(bounded_eval["case_ids"])

        team_skill_path = tmp_path / "team_skill.yaml"
        _write_yaml(team_skill_path, {"name": "test_team_skill", "version": "1.0"})
        harness_refs_path = tmp_path / "harness_refs.yaml"
        _write_yaml(harness_refs_path, {"name": "test_harness", "version": "1.0"})
        output_dir = tmp_path / "analysis"

        from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.schema import EvaluationResultAnalysisInvocation

        analyzer = EvaluationResultAnalyzer(
            EvaluationResultAnalyzerConfig(
                model_config_ref=str(model_config_path),
                output_filename="team_issues.yaml",
            ),
            experience_learner=_FakeExperienceLearner(),
        )
        invocation = EvaluationResultAnalysisInvocation(
            eval_ref_path=str(bounded_eval["eval_ref_path"]),
            case_results_dir=str(bounded_eval["case_results_dir"]),
            case_traces_dir=str(bounded_eval["case_results_dir"]),
            team_skill_ref_path=str(team_skill_path),
            harness_refs_path=str(harness_refs_path),
            output_dir=str(output_dir),
        )

        analysis_ref_path = await analyzer.analyze(invocation)

        analysis_ref = yaml.safe_load(Path(analysis_ref_path).read_text(encoding="utf-8"))
        issues_path = Path(analysis_ref["issues_path"])
        issues_payload = yaml.safe_load(issues_path.read_text(encoding="utf-8"))

        # Output files exist
        assert Path(analysis_ref_path).is_file(), "analysis_ref.yaml should exist"
        assert issues_path.is_file(), "issues output file should exist"

        # Analysis status is valid
        status = analysis_ref["metadata"]["analysis_status"]
        assert status in {"completed", "partial"}, f"unexpected analysis_status: {status}"

        # Issues structure
        issues = analysis_ref["issues"]
        assert isinstance(issues, list), "issues should be a list"
        assert issues_payload["issues"] == issues

        # Per-issue field validation
        for i, issue in enumerate(issues):
            assert issue.get("issue_id"), f"issue[{i}] missing issue_id"
            assert issue.get("category") in {"member_harness", "team_coordination"}, (
                f"issue[{i}] invalid category: {issue.get('category')}"
            )
            assert issue.get("severity") in {"high", "medium", "low"}, (
                f"issue[{i}] invalid severity: {issue.get('severity')}"
            )
            assert issue.get("summary", "").strip(), f"issue[{i}] empty summary"
            assert issue.get("optimization_target") in {"team_skill", "member_harness"}, (
                f"issue[{i}] invalid optimization_target: {issue.get('optimization_target')}"
            )
            assert issue.get("recommendation", "").strip(), f"issue[{i}] empty recommendation"

            for case_id in issue.get("affected_cases", []):
                assert case_id in all_real_case_ids, (
                    f"issue[{i}] affected_case '{case_id}' not in evaluations directory"
                )
            for j, ev in enumerate(issue.get("evidence", [])):
                assert "case_id" in ev, f"issue[{i}] evidence[{j}] missing case_id"
                assert ev["case_id"] in all_real_case_ids, (
                    f"issue[{i}] evidence[{j}] case_id '{ev['case_id']}' not in evaluations"
                )

        # Metadata completeness
        metadata = analysis_ref["metadata"]
        assert metadata.get("strategy") == "diagnosis_agent", "strategy should be diagnosis_agent"
        assert "signals_method" in metadata, "metadata missing signals_method"
        assert metadata.get("per_case_count") == len(all_real_case_ids), (
            f"per_case_count {metadata.get('per_case_count')} != {len(all_real_case_ids)}"
        )

        # Source path preservation
        assert analysis_ref.get("source_eval_ref_path") == str(bounded_eval["eval_ref_path"]), (
            "source_eval_ref_path mismatch"
        )


def _write_evaluation_artifacts(tmp_path: Path, *, method: str) -> dict[str, Path]:
    eval_dir = tmp_path / "evaluation"
    case_results_dir = eval_dir / "case_results"
    case_dir = case_results_dir / "case_001"
    judge_dir = case_dir / "judge"
    artifacts_dir = case_dir / "artifacts"
    judge_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Placeholder artifact so agent list_files("artifacts") returns a non-empty result
    (artifacts_dir / "response.txt").write_text("Agent final response: The answer is 3.\n", encoding="utf-8")

    summary_path = eval_dir / "summary.json"
    result_path = case_dir / "result.json"
    trace_path = case_dir / "trace.json"
    normalized_trace_path = judge_dir / "normalized_trace.json"
    eval_ref_path = eval_dir / "eval_ref.yaml"

    dimensions = {
        "per_behavior_scores": {"b1": 0.2, "b2": 0.64},
        "low_score_behaviors": ["b1"],
        "avg_behavior_score": 0.42,
        "pass_count": 1,
        "fail_count": 1,
    }
    behaviors = [
        {"id": "b1", "score": 0.2, "reason": "expected output not produced", "evidence": "step_3"},
        {"id": "b2", "score": 0.64, "reason": "partially correct", "evidence": ""},
    ]
    evaluation = {
        "method": method,
        "score": 0.42,
        "passed": False,
        "reason": "required behavior b1 was not satisfied",
        "metadata": {
            "parsed": {
                "overall_reason": "wrong arithmetic result",
                "behaviors": behaviors if method == "llm_as_judge" else [],
                "dimensions": dimensions,
            },
            "dimensions": dimensions,
        },
    }
    _write_json(
        summary_path,
        {
            "total_cases": 1,
            "passed_cases": 0,
            "failed_cases": 1,
            "average_score": 0.42,
            "evaluation_method": method,
        },
    )
    _write_json(
        result_path,
        {
            "case_id": "case_001",
            "status": "passed",
            "score": 0.42,
            "evaluation": evaluation,
            "result": "3",
            "error": None,
            "trace_path": str(trace_path),
            "artifacts": {},
            "metadata": {
                "training_signal": {
                    "expected_failure_modes": ["wrong_arithmetic_result"],
                    "capability_gap": "needs deterministic arithmetic validation",
                    "target_surfaces": ["tool"],
                    "difficulty_rationale": "medium because it checks basic calculation reliability",
                }
            },
        },
    )
    _write_json(
        trace_path,
        {
            "case_id": "case_001",
            "status": "passed",
            "input": "solve 1 + 1",
            "response": "3",
            "evaluation": evaluation,
        },
    )
    # normalized_trace.json lives at judge/normalized_trace.json inside the case dir.
    # The per-case diagnosis agent workspace is case_dir; it reads this file via the
    # workspace-relative path "judge/normalized_trace.json".
    _write_json(
        normalized_trace_path,
        {
            "case_id": "case_001",
            "traces": [
                {
                    "trace_id": "trace_001",
                    "member_id": "math_teacher",
                    "member_role": "math_teacher",
                    "step_count": 4,
                    "message_count": 6,
                    "messages": [
                        {"role": "user", "content": "solve 1 + 1"},
                        {"role": "assistant", "content": "The answer is 3"},
                    ],
                }
            ],
        },
    )
    _write_yaml(
        eval_ref_path,
        {
            "eval_id": "eval_001",
            "summary_path": str(summary_path),
            "case_results_dir": str(case_results_dir),
            "case_traces_dir": str(case_results_dir),
            "cases": [
                {
                    "case_id": "case_001",
                    "case_path": str(tmp_path / "dataset" / "case_001.json"),
                    "trace_path": str(trace_path),
                    "result_path": str(result_path),
                    "status": "passed",
                    "score": 0.42,
                }
            ],
        },
    )
    return {
        "eval_ref_path": eval_ref_path,
        "summary_path": summary_path,
        "case_results_dir": case_results_dir,
        "result_path": result_path,
        "trace_path": trace_path,
        "normalized_trace_path": normalized_trace_path,
        "artifacts_dir": artifacts_dir,
    }


def _write_empty_eval_ref(tmp_path: Path) -> Path:
    eval_ref_path = tmp_path / "eval_ref.yaml"
    _write_yaml(
        eval_ref_path,
        {
            "eval_id": "eval_empty",
            "summary_path": "",
            "case_results_dir": str(tmp_path / "empty_case_results"),
            "case_traces_dir": str(tmp_path / "empty_case_results"),
            "cases": [],
        },
    )
    return eval_ref_path


def _summary_input(*, method: str) -> Any:
    from openjiuwen.rsi.evaluation_result_analyzer.case_reader import EvaluationSummaryInput

    return EvaluationSummaryInput(
        total_cases=2,
        passed_count=0,
        failed_count=2,
        average_score=0.25,
        evaluation_method=method,
    )


def _case_input(
    *,
    case_id: str,
    method: str,
    status: str,
    passed: bool,
    expected: str | None = "2",
    response: str = "3",
    error: str = "",
    metadata: dict[str, Any] | None = None,
    normalized_trace_summary: dict[str, Any] | None = None,
) -> Any:
    from openjiuwen.rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput

    return CaseAnalysisInput(
        case_id=case_id,
        status=status,
        score=0.25,
        input="solve 1 + 1",
        expected=expected,
        response=response,
        error=error,
        evaluation_method=method,
        evaluation_passed=passed,
        evaluation_reason="answer did not satisfy required behavior",
        evaluation_metadata=metadata or {},
        trace_path=f"case_results/{case_id}/trace.json",
        result_path=f"case_results/{case_id}/result.json",
        normalized_trace_summary=normalized_trace_summary or {},
    )


def _resolve_case_results_dir(eval_ref: dict[str, Any], evaluations_dir: Path) -> Path:
    case_results_str = eval_ref.get("case_results_dir", "")
    if case_results_str:
        candidate = Path(case_results_str)
        if not candidate.is_absolute():
            candidate = evaluations_dir / candidate
        if candidate.is_dir():
            return candidate
    return evaluations_dir / "case_results"


def _real_model_max_cases() -> int:
    raw = os.environ.get("AUTO_COORDINATING_ANALYZER_MAX_REAL_CASES", "").strip()
    if not raw:
        return 3
    try:
        value = int(raw)
    except ValueError:
        return 3
    return max(1, value)


def _materialize_bounded_evaluation_dir(
    *,
    eval_ref: dict[str, Any],
    evaluations_dir: Path,
    case_result_files: list[Path],
    output_root: Path,
    max_cases: int,
) -> dict[str, Any]:
    selected_results = _select_bounded_case_results(case_result_files, max_cases=max_cases)
    bounded_dir = output_root / "evaluations"
    bounded_case_results_dir = bounded_dir / "case_results"
    bounded_case_results_dir.mkdir(parents=True, exist_ok=True)

    bounded_cases: list[dict[str, Any]] = []
    scores: list[float] = []
    passed_count = 0
    failed_count = 0
    evaluation_method = ""

    eval_cases_by_id = {
        str(item.get("case_id", "")): item for item in eval_ref.get("cases", []) if isinstance(item, dict)
    }
    for result_path in selected_results:
        case_id = result_path.parent.name
        bounded_case_dir = bounded_case_results_dir / case_id
        if bounded_case_dir.exists():
            shutil.rmtree(bounded_case_dir)
        shutil.copytree(result_path.parent, bounded_case_dir)

        copied_result_path = bounded_case_dir / "result.json"
        copied_trace_path = bounded_case_dir / "trace.json"
        result_data = json.loads(copied_result_path.read_text(encoding="utf-8"))
        evaluation = result_data.get("evaluation") or {}
        score = float(result_data.get("score") or evaluation.get("score") or 0.0)
        scores.append(score)
        if not evaluation_method:
            evaluation_method = str(evaluation.get("method", ""))
        if result_data.get("status") == "passed" and evaluation.get("passed") is True:
            passed_count += 1
        else:
            failed_count += 1

        original_case = eval_cases_by_id.get(case_id, {})
        bounded_cases.append(
            {
                "case_id": case_id,
                "case_path": str(original_case.get("case_path", "")),
                "trace_path": str(copied_trace_path),
                "result_path": str(copied_result_path),
                "status": str(result_data.get("status", "")),
                "score": score,
                "metadata": dict(original_case.get("metadata") or {}),
            }
        )

    summary_path = bounded_dir / "summary.json"
    average_score = sum(scores) / len(scores) if scores else 0.0
    _write_json(
        summary_path,
        {
            "total_cases": len(selected_results),
            "passed_cases": passed_count,
            "failed_cases": failed_count,
            "average_score": average_score,
            "evaluation_method": evaluation_method,
        },
    )
    eval_ref_path = bounded_dir / "eval_ref.yaml"
    _write_yaml(
        eval_ref_path,
        {
            "eval_id": f"{eval_ref.get('eval_id', 'eval')}_bounded",
            "created_at": eval_ref.get("created_at", ""),
            "team_name": eval_ref.get("team_name", ""),
            "team_skill_ref_path": eval_ref.get("team_skill_ref_path", ""),
            "harness_refs_path": eval_ref.get("harness_refs_path", ""),
            "dataset": eval_ref.get("dataset", {}),
            "eval_dir": str(bounded_dir),
            "case_results_dir": str(bounded_case_results_dir),
            "case_traces_dir": str(bounded_case_results_dir),
            "summary_path": str(summary_path),
            "cases": bounded_cases,
            "source_eval_ref_path": str(evaluations_dir / "eval_ref.yaml"),
            "metadata": {"bounded_from_real_eval": True, "max_cases": max_cases},
        },
    )
    return {
        "eval_ref_path": eval_ref_path,
        "case_results_dir": bounded_case_results_dir,
        "case_ids": [path.parent.name for path in selected_results],
    }


def _select_bounded_case_results(case_result_files: list[Path], *, max_cases: int) -> list[Path]:
    prioritized = sorted(case_result_files, key=_case_result_priority)
    return prioritized[:max_cases]


def _case_result_priority(result_path: Path) -> tuple[int, float, str]:
    try:
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return (0, 0.0, result_path.parent.name)
    evaluation = result_data.get("evaluation") or {}
    score = float(result_data.get("score") or evaluation.get("score") or 0.0)
    is_failed = result_data.get("status") != "passed" or evaluation.get("passed") is not True
    return (0 if is_failed else 1, score, result_path.parent.name)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
