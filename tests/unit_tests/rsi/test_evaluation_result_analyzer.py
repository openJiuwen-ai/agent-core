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


class _FakeIssueStrategy:
    async def analyze(self, invocation):  # type: ignore[no-untyped-def]
        from openjiuwen.rsi.harness_rsi.schema import (
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
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig

        config = EvaluationResultAnalyzerConfig.from_dict(
            {
                "model_config_ref": "models/analyzer.yaml",
                "diagnosis_agent_model_config_ref": "models/diagnosis.yaml",
                "diagnosis_agent_max_retries": 3,
                "diagnosis_agent_max_concurrency": 7,
                "diagnosis_agent_max_iterations": 25,
                "diagnosis_agent_max_tokens": 12288,
                "causal_investigation_required": False,
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
        assert config.diagnosis_agent_max_tokens == 12288
        assert config.causal_investigation_required is False
        assert EvaluationResultAnalyzerConfig().causal_investigation_required is True
        assert config.max_issues == 8
        assert config.evidence_limit_per_issue == 3
        assert config.output_filename == "issues.yaml"

    def test_interfaces_expose_strategy_and_signal_extractor_protocols(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.interfaces import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
            _build_evidence_summary,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseReader

        reader = CaseReader()

        assert reader.read_case_inputs(str(tmp_path / "missing_case_results")) == []

    def test_missing_eval_ref_raises_value_error(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseReader

        reader = CaseReader()

        with pytest.raises(ValueError):
            reader.read_eval_ref(str(tmp_path / "missing_eval_ref.yaml"))


class TestSignalExtractors:
    """Method-aware deterministic signal extraction contracts."""

    def test_build_signal_extractor_dispatches_by_method(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.signal_extractor import (
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

    def test_json_extraction_ignores_braces_inside_strings(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        parsed = analyzer_module._extract_json_object(
            'Reasoning first. {"diagnoses":[{"root_cause":"literal } in source"}]}'
        )

        assert parsed == {"diagnoses": [{"root_cause": "literal } in source"}]}

    def test_truncated_diagnosis_json_is_retryable(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.model_call import RetryableModelOutputError

        error = analyzer_module._unusable_diagnosis_output_error(
            "case_001",
            [
                'Analysis before JSON. {"diagnoses":[{"issue_ca',
                'Still truncated. {"diagnoses":[{"summary":"unfinished',
            ],
        )

        assert isinstance(error, RetryableModelOutputError)
        assert "incomplete JSON after repair" in str(error)

    def test_non_json_service_error_remains_non_retryable(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        error = analyzer_module._unusable_diagnosis_output_error(
            "case_001",
            ["Error code: 401 - invalid_api_key"],
        )

        assert isinstance(error, ValueError)
        assert not isinstance(error, analyzer_module._DiagnosisOutputFormatError)
        assert "contained a model-service error" in str(error)

    def test_non_json_diagnosis_prose_is_a_format_error(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        error = analyzer_module._unusable_diagnosis_output_error(
            "case_001",
            ["Confirmed. The verifier flagged a residual raw source header."],
        )

        assert isinstance(error, analyzer_module._DiagnosisOutputFormatError)
        assert "did not contain JSON" in str(error)

    def test_build_analysis_strategy_returns_diagnosis_agent_strategy(self) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import build_analysis_strategy

        strategy = build_analysis_strategy(EvaluationResultAnalyzerConfig())

        assert strategy.name == "diagnosis_agent"

    def test_strategy_protocol_accepts_single_invocation(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.interfaces import (
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
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

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
        assert captured["model_data"]["model_request_config"]["max_tokens"] == 16384
        assert agent["model"] == "fake-model"
        rails = captured["agent_kwargs"]["rails"]
        assert rails == []
        assert captured["agent_kwargs"]["enable_sys_operation"] is False

    @pytest.mark.asyncio
    async def test_analyze_diagnoses_only_nonpassing_cases(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.schema import (
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
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False),
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
        causal_evidence_path = Path(artifact.metadata["causal_evidence_path"])
        causal_evidence = json.loads(causal_evidence_path.read_text(encoding="utf-8"))
        assert causal_evidence["schema_version"] == 2
        assert [case["case_id"] for case in causal_evidence["cases"]] == ["unresolved"]
        assert causal_evidence["cases"][0]["causal_digest"]["outcome"]["case_id"] == "unresolved"

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_uses_case_dir_as_workspace(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(
                model_config_ref="unused.yaml",
                diagnosis_agent_max_concurrency=2,
                causal_investigation_required=False,
            ),
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
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
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
    async def test_per_case_diagnosis_raises_when_agent_returns_service_error_text(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
        )

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            return "Error code: 401 - invalid_api_key"

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        with pytest.raises(ValueError, match="contained a model-service error"):
            await strategy._per_case_diagnosis(
                [case],
                DeterministicSignals(method="script_based"),
                None,
            )

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_isolates_one_case_format_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        cases: list[CaseAnalysisInput] = []
        for case_id in ("case_bad", "case_good"):
            case_dir = tmp_path / "case_results" / case_id
            result_path = case_dir / "result.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text("{}", encoding="utf-8")
            cases.append(
                CaseAnalysisInput(
                    case_id=case_id,
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
            )

        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
        )

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            if "case_bad" in agent["workspace"]:
                return "Diagnosis prose without a JSON object."
            return json.dumps(
                {
                    "diagnoses": [
                        {
                            "issue_category": "unassigned",
                            "severity": "low",
                            "summary": "No optimizable cause is supported.",
                            "failure_mode": "insufficient_evidence",
                            "failure_cluster": {
                                "failed_checks": ["check_good"],
                                "observable_behavior": "the check remains unresolved",
                            },
                            "root_cause": "Evidence does not separate the mechanisms.",
                            "critical_mistake": "No evidence-backed decision is available.",
                            "general_mechanism": "Collect a discriminator before optimization.",
                            "target_ref": "unassigned",
                            "evidence_refs": [],
                            "affected_components": [],
                            "recommendation": "Keep this diagnosis unassigned.",
                            "decision_contract": {
                                "acceptance_observable": "the check remains unresolved",
                                "activation_phase": "during_investigation",
                            },
                            "confidence": "low",
                        }
                    ]
                }
            )

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)

        results = await strategy._per_case_diagnosis(
            cases,
            DeterministicSignals(method="script_based"),
            None,
        )

        assert [item["case_id"] for item in results] == ["case_bad", "case_good"]
        assert results[0]["analysis_failed"] is True
        assert results[0]["diagnosis_error_type"] == "output_format"
        assert results[1]["failure_mode"] == "insufficient_evidence"

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_runs_cases_sequentially(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
                causal_investigation_required=False,
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
    async def test_insufficient_case_is_supplemented_before_next_case(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        cases = []
        for case_id in ("case_a", "case_b"):
            case_dir = tmp_path / "case_results" / case_id
            result_path = case_dir / "result.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text("{}", encoding="utf-8")
            normalized_trace = case_dir / "judge" / "normalized_trace.json"
            normalized_trace.parent.mkdir(parents=True)
            messages = []
            if case_id == "case_a":
                messages = [
                    {
                        "role": "assistant",
                        "message_index": 7,
                        "step_pointer": "trial_1:message_7",
                        "content": "",
                        "tool_calls": [
                            {
                                "name": "read_file",
                                "input": '{"path":"contract.txt"}',
                                "output": "SUPPLEMENT_MARKER conflicting deadline is stated in the contract",
                                "error": "",
                                "step_pointer": "trial_1:message_7",
                            }
                        ],
                    }
                ]
            normalized_trace.write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "traces": [{"trace_id": f"{case_id}:trial_1", "messages": messages}],
                    }
                ),
                encoding="utf-8",
            )
            cases.append(
                CaseAnalysisInput(
                    case_id=case_id,
                    status="failed",
                    score=0.0,
                    input=f"{case_id} input",
                    expected=None,
                    response=f"{case_id} response",
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
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
        )
        call_order: list[str] = []

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        def diagnosis(evidence_status: str) -> dict[str, Any]:
            assigned = evidence_status == "confirmed"
            return {
                "diagnoses": [
                    {
                        "issue_category": "member_harness" if assigned else "unassigned",
                        "severity": "medium",
                        "summary": "deadline conflict diagnosis",
                        "failure_mode": "wrong_deadline_decision",
                        "evidence_status": evidence_status,
                        "failed_requirement": "deadline requirement",
                        "competing_hypotheses": ["contract deadline conflict"],
                        "discriminating_evidence": "inspect the contract deadline",
                        "root_cause": "deadline conflict" if assigned else "not yet distinguished",
                        "critical_mistake": "selected the wrong deadline" if assigned else "unknown",
                        "general_mechanism": "resolve conflicting source requirements",
                        "target_ref": "member_harness.solver.prompt" if assigned else "unassigned",
                        "evidence_refs": ([{"step_pointer": "trial_1:message_7"}] if assigned else []),
                        "affected_components": ["solver"] if assigned else [],
                        "recommendation": "compare governing sources before answering",
                        "confidence": "high" if assigned else "low",
                    }
                ]
            }

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            del agent, max_retries
            if "inside the Analyzer, before any Harness candidate" in prompt:
                call_order.append("case_a_supplement")
                assert "SUPPLEMENT_MARKER" in prompt
                return json.dumps(diagnosis("confirmed"))
            if "case_a input" in prompt:
                call_order.append("case_a_initial")
                return json.dumps(diagnosis("insufficient"))
            call_order.append("case_b_initial")
            return json.dumps(diagnosis("confirmed"))

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
        monkeypatch.setattr(
            analyzer_module,
            "_case_diagnoses_validation_conflicts",
            lambda *args, **kwargs: [],
        )

        results = await strategy._per_case_diagnosis(
            cases,
            DeterministicSignals(method="llm_as_judge"),
            None,
        )

        assert call_order == ["case_a_initial", "case_a_supplement", "case_b_initial"]
        assert results[0]["case_id"] == "case_a"
        assert results[0]["evidence_status"] == "confirmed"
        assert results[0]["evidence_supplement"]["status"] == "resolved"
        assert results[1]["case_id"] == "case_b"
        assert results[1]["evidence_supplement"]["status"] == "not_needed"

    def test_compactor_omission_claim_forces_raw_evidence_supplement(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _diagnoses_need_evidence_supplement,
        )

        diagnoses = [
            {
                "evidence_status": "confirmed",
                "discriminating_evidence": (
                    "The displayed tool response contained ...[omitted 2319 chars]... "
                    "where the controlling clause should appear."
                ),
            }
        ]

        assert _diagnoses_need_evidence_supplement(diagnoses) is True

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_records_retryable_empty_output_without_aborting(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )
        from openjiuwen.rsi.harness_rsi.model_call import RetryableModelOutputError

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
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
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
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
                causal_investigation_required=False,
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
    async def test_non_json_correction_still_enters_outcome_independent_recovery(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        case_dir = tmp_path / "case_results" / "case_recovery"
        result_path = case_dir / "result.json"
        trace_path = case_dir / "trace.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        trace_path.write_text(json.dumps({"case_id": "case_recovery", "events": []}), encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_recovery",
            status="failed",
            score=0.0,
            input="Inspect the task-visible evidence and answer.",
            expected=None,
            response="A released conclusion.",
            error="",
            evaluation_method="llm_as_judge",
            evaluation_passed=False,
            evaluation_reason="one opaque criterion failed",
            evaluation_metadata={
                "judge_evidence": {
                    "criteria": [
                        {
                            "criterion_id": "opaque",
                            "score": 0.0,
                            "status": "failed",
                            "rationale": "the evaluator-owned label differs",
                        }
                    ]
                }
            },
            trace_path=str(trace_path),
            result_path=str(result_path),
        )
        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=True)
        )

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        calls = 0

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return json.dumps(
                    {
                        "causal_investigation": {
                            "hypotheses": [
                                {
                                    "hypothesis_id": "h_leak",
                                    "claim": "The expected answer requires another conclusion.",
                                    "explains_requirement_ids": ["criterion:opaque"],
                                    "falsified_if": "The target result agrees.",
                                    "evidence_requests": [],
                                }
                            ]
                        }
                    }
                )
            if calls == 2:
                return "correction was not JSON"
            assert "CAUSAL_INVESTIGATION_PHASE=outcome_independent_recovery" in prompt
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": f"h{index}",
                                "claim": f"Runtime mechanism {index} released an unsupported decision ground.",
                                "explains_requirement_ids": ["criterion:opaque"],
                                "current_support": [],
                                "falsified_if": f"The trace verifies decision ground {index} before release.",
                                "evidence_requests": [
                                    {
                                        "request_id": f"q{index}",
                                        "operation": "search_trace",
                                        "query": f"decision ground {index} release",
                                    }
                                ],
                            }
                            for index in (1, 2)
                        ]
                    }
                }
            )

        def entered_evidence_execution(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("entered task-visible evidence execution")

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
        monkeypatch.setattr(analyzer_module, "execute_causal_investigation", entered_evidence_execution)

        with pytest.raises(RuntimeError, match="entered task-visible evidence execution"):
            await strategy._per_case_diagnosis(
                [case],
                DeterministicSignals(method="llm_as_judge"),
                None,
            )
        assert calls == 3

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_repairs_deterministic_evidence_conflict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
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
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation

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
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
        )

        async def fake_per_case(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"case_id": "case_001", "issue_category": "member_harness"}]

        async def fake_aggregate(*args: Any, **kwargs: Any) -> list[Any]:
            raise RuntimeError("aggregation failed")

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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

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

    @pytest.mark.asyncio
    async def test_run_agent_does_not_replay_prose_for_service_retry_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.core.runner import Runner
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        prompts: list[str] = []

        async def fake_run_agent(*args: Any, **kwargs: Any) -> str:
            prompts.append(kwargs["inputs"]["query"])
            return "I will analyze this case in prose instead of returning JSON."

        monkeypatch.setattr(Runner, "run_agent", fake_run_agent)

        raw = await analyzer_module._run_agent(object(), "large evidence prompt", max_retries=20)

        assert raw.startswith("I will analyze")
        assert len(prompts) == 2
        assert prompts[0] == "large evidence prompt"
        assert "FORMAT-ONLY TASK" in prompts[1]

    def test_json_repair_prompt_is_format_only_and_keeps_completed_analysis(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        original = "ORIGINAL-EVIDENCE " * 10_000
        conclusion = "The first wrong decision was submitting before checking all receipts."

        prompt = analyzer_module._build_json_repair_prompt(original, conclusion)

        assert "FORMAT-ONLY TASK" in prompt
        assert conclusion in prompt
        assert "ORIGINAL-EVIDENCE" not in prompt
        assert '"diagnoses"' in prompt
        assert '"evidence_relation"' in prompt
        assert '"evidence_independence"' in prompt

    def test_handoff_repair_preserves_entailment_fields(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        prompt = analyzer_module._build_causal_handoff_repair_prompt(
            public_task_contract="Use the declared public mode.",
            diagnoses=[{"hypothesis_assessment": [{"hypothesis_id": "h1", "status": "falsified"}]}],
            investigation={"hypotheses": [], "evidence_requests": []},
            evidence_results={"results": []},
            audit={"diagnosis_audits": []},
        )

        assert "evidence_relation" in prompt
        assert "evidence_independence" in prompt
        assert 'evidence_relation="direct_falsifier"' in prompt

    def test_entailment_audit_json_repair_is_format_only(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        prompt = analyzer_module._build_hypothesis_entailment_audit_json_repair_prompt(
            "large frozen audit input",
            "h1 was rejected because the cited result came from the questioned mechanism.",
        )

        assert "FORMAT-ONLY TASK" in prompt
        assert '"assessment_audits"' in prompt
        assert '"evidence_entails_status"' in prompt
        assert '"evidence_independent"' in prompt
        assert "large frozen audit input" not in prompt


class TestDiagnosisPromptEvidenceSummary:
    """Verify per-case diagnosis prompt consumes bounded evidence, not raw case dirs."""

    def _make_case_input(self, result_path: str) -> Any:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            DeterministicSignals,
        )

        return DeterministicSignals(method="llm_as_judge")

    def test_inline_payload_uses_compact_json(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module

        payload = analyzer_module._build_diagnosis_input_json(
            case=self._make_case_input(str(tmp_path / "result.json")),
            signals=self._make_signals(),
            retrieved_experience=None,
            evidence_summary_available=False,
        )

        assert '\n  "authoritative_task_contract"' not in payload
        assert payload.startswith('{"analysis_protocol":')

    def test_prompt_contains_evidence_summary_when_available(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        assert '"evidence_summary_available":true' in prompt
        assert "Analyze concrete member harness capability" in prompt
        assert "member_harness.<role>.<variable>" in prompt
        assert "judge/normalized_trace.json" not in prompt
        assert "artifacts" not in prompt

    def test_prompt_uses_inline_json_when_summary_is_missing(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        assert '"evidence_summary_available":false' in prompt
        assert "Analyze team organization" in prompt

    def test_prompt_does_not_contain_absolute_case_dir_path(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
            _case_prior_candidate_feedback,
        )

        result_path = tmp_path / "case_feedback" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")

        paired_feedback = {
            "by_case": {
                "case_001": [
                    {
                        "schema_version": 2,
                        "prediction": {
                            "causal_intervention_contracts": [
                                {
                                    "source_causal_hypothesis_id": "h1",
                                    "predicted_behavior_and_outcome": "state_b becomes valid",
                                }
                            ]
                        },
                        "activation": {"availability": "observed", "state": "triggered"},
                        "observed_outcome": {
                            "strict_score": {"source": 0.0, "candidate": 0.0, "delta": 0.0},
                            "continuous_score": {"source": 0.2, "candidate": 0.8, "delta": 0.6},
                            "requirement_delta": {
                                "newly_passed_fail_to_pass": ["state_a"],
                                "remaining_failed_fail_to_pass": ["state_b"],
                            },
                        },
                    }
                ],
                "unrelated_case": [{"experiment_id": "must_not_leak"}],
            }
        }
        case_feedback = _case_prior_candidate_feedback(paired_feedback, "case_001")
        payload = json.loads(
            _build_diagnosis_input_json(
                case=self._make_case_input(str(result_path)),
                signals=self._make_signals(),
                retrieved_experience=None,
                evidence_summary_available=True,
                prior_candidate_feedback=case_feedback,
            )
        )

        assert payload["prior_candidate_feedback"]["experiments"][0]["verifier_delta"][
            "remaining_failed_fail_to_pass"
        ] == ["state_b"]
        experiment = payload["prior_candidate_feedback"]["experiments"][0]
        assert (
            experiment["prediction"]["causal_intervention_contracts"][0]["predicted_behavior_and_outcome"]
            == "state_b becomes valid"
        )
        assert experiment["activation"]["state"] == "triggered"
        assert experiment["observed_outcome"]["strict_score"]["delta"] == 0.0
        assert experiment["observed_outcome"]["continuous_score"]["delta"] == 0.6
        assert experiment["observed_outcome"]["requirement_delta"]["remaining_failed_fail_to_pass"] == ["state_b"]
        assert payload["prior_candidate_feedback"]["case_id"] == "case_001"
        assert "must_not_leak" not in json.dumps(payload["prior_candidate_feedback"])
        assert "Preserve newly passing operations" in payload["prior_candidate_feedback_policy"]

    def test_diagnosis_input_preserves_complete_authoritative_task(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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

        assert payload["analysis_protocol"]["version"] == "generic_behavior_causal_v19"
        assert payload["retrieved_experience"]["matches"][0]["component_layer"] == "prompt_section"
        assert payload["experience_usage_policy"]["must_use_current_evidence_first"] is True
        assert "Do not copy a historical target_ref" in payload["experience_usage_policy"]["rules"][0]

    def test_system_prompt_uses_generic_evidence_grounded_protocol(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            DIAGNOSIS_SYSTEM_PROMPT,
        )

        assert "trace.json" in DIAGNOSIS_SYSTEM_PROMPT
        assert "result.json" in DIAGNOSIS_SYSTEM_PROMPT
        assert "repository/" in DIAGNOSIS_SYSTEM_PROMPT
        assert "code, documents, spreadsheets, search" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Scores establish that an outcome failed" in DIAGNOSIS_SYSTEM_PROMPT
        assert "score alone never establishes why" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Compare at least two plausible explanations" in DIAGNOSIS_SYSTEM_PROMPT
        assert "evidence_status" in DIAGNOSIS_SYSTEM_PROMPT
        assert "supported_hypothesis" in DIAGNOSIS_SYSTEM_PROMPT
        assert "same aggregate score by itself is not new causal evidence" in DIAGNOSIS_SYSTEM_PROMPT
        assert "case-root `trace.json`" in DIAGNOSIS_SYSTEM_PROMPT
        assert "authoritative_benchmark_test_contract.test_patch" in DIAGNOSIS_SYSTEM_PROMPT
        assert "evidence_summary.md" in DIAGNOSIS_SYSTEM_PROMPT
        assert "no-exception smoke probe" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Requirement-classification audit" in DIAGNOSIS_SYSTEM_PROMPT
        assert "false rejection" in DIAGNOSIS_SYSTEM_PROMPT
        assert "incorporation by" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Do not convert a Tool, Skill, Config" in DIAGNOSIS_SYSTEM_PROMPT
        assert "member_harness.<role>.execution_budget" in DIAGNOSIS_SYSTEM_PROMPT
        assert "member_harness.<role>.rail" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Failed-requirement coverage (hard)" in DIAGNOSIS_SYSTEM_PROMPT
        assert "task_sufficient" in DIAGNOSIS_SYSTEM_PROMPT
        assert "counterfactual_prediction" in DIAGNOSIS_SYSTEM_PROMPT
        assert "public task contract is available" in DIAGNOSIS_SYSTEM_PROMPT
        assert "do not claim it is unavailable" in DIAGNOSIS_SYSTEM_PROMPT

    def test_causal_prompt_preserves_late_controller_results_under_independent_budgets(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _causal_prompt_json

        rendered = _causal_prompt_json(
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h1",
                        "claim": "The first mechanism explains the failure.",
                        "falsified_if": "A decisive artifact contradicts it.",
                        "current_support": ["X" * 20_000],
                    }
                ]
            },
            {
                "results": [
                    {
                        "request_id": "q1",
                        "operation": "read_event",
                        "availability": "available",
                        "event": {"content": "A" * 20_000, "tool_calls": []},
                    },
                    {
                        "request_id": "q2",
                        "operation": "read_artifact_window",
                        "availability": "available",
                        "source": "artifacts/decisive.txt",
                        "text": "LATE_DECISIVE_EVIDENCE",
                    },
                ]
            },
        )
        payload = json.loads(rendered)

        results = payload["controller_evidence_results"]["results"]
        assert [item["request_id"] for item in results] == ["q1", "q2"]
        assert results[1]["text"] == "LATE_DECISIVE_EVIDENCE"
        assert "current_support" not in payload["investigation"]["hypotheses"][0]

    def test_input_builds_complete_failed_requirement_inventory(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )

        result_path = tmp_path / "case_requirements" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        case = replace(
            self._make_case_input(str(result_path)),
            evaluation_metadata={
                "judge_evidence": {
                    "criteria": [
                        {
                            "criterion_id": "correct_value",
                            "score": 0.0,
                            "status": "ok",
                            "rationale": "the produced value differs from the required value",
                        },
                        {
                            "criterion_id": "artifact_exists",
                            "score": 1.0,
                            "status": "ok",
                            "rationale": "the artifact exists",
                        },
                    ]
                }
            },
        )

        payload = json.loads(
            _build_diagnosis_input_json(
                case=case,
                signals=self._make_signals(),
                retrieved_experience=None,
                evidence_summary_available=False,
            )
        )

        inventory = payload["deterministic_failed_requirement_inventory"]
        assert [item["requirement_id"] for item in inventory["items"]] == ["criterion:correct_value"]
        assert "does not identify their causes" in inventory["policy"]

    def test_failed_requirement_inventory_does_not_drop_criteria_after_twenty_four(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _build_failed_requirement_inventory

        result_path = tmp_path / "case_many_requirements" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        case = replace(
            self._make_case_input(str(result_path)),
            evaluation_metadata={
                "judge_evidence": {
                    "criteria": [
                        {
                            "criterion_id": f"requirement_{index}",
                            "score": 0.0,
                            "status": "failed",
                            "rationale": f"requirement {index} failed",
                        }
                        for index in range(30)
                    ]
                }
            },
        )

        inventory = _build_failed_requirement_inventory(case)

        assert len(inventory["items"]) == 30
        assert inventory["items"][-1]["requirement_id"] == "criterion:requirement_29"

    def test_causal_coverage_rejects_local_cause_claimed_as_complete(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        inventory = {
            "items": [
                {"requirement_id": "criterion:value"},
                {"requirement_id": "criterion:format"},
            ]
        }
        diagnosis = {
            "evidence_status": "confirmed",
            "target_ref": "member_harness.solver.prompt",
            "failed_requirement": "the output value is wrong",
            "discriminating_evidence": "the trace shows the decision and resulting value",
            "evidence_refs": [{"trace_id": "t", "role": "solver", "message_index": 2}],
            "failure_cluster": {
                "failed_checks": ["criterion:value"],
                "observable_behavior": "the value is wrong",
            },
            "causal_coverage": {
                "explained_requirement_ids": ["criterion:value"],
                "residual_requirement_ids": ["criterion:format"],
                "unexplained_observations": ["format remains wrong"],
                "causal_chain": [
                    {
                        "cause": "the solver chose the wrong value",
                        "effect": "the value criterion failed",
                        "evidence_status": "observed",
                        "evidence_refs": [],
                    }
                ],
                "counterfactual_prediction": "the value changes while formatting remains unchanged",
                "sufficiency_status": "task_sufficient",
            },
            "decision_contract": {"acceptance_observable": "the value criterion passes"},
            "confidence": "high",
        }

        conflicts = _diagnosis_validation_conflicts(
            diagnosis,
            {},
            failed_requirement_inventory=inventory,
        )

        assert any(
            "task_sufficient requires all inventory IDs in this diagnosis's failure cluster" in item
            for item in conflicts
        )

    def test_causal_coverage_accepts_explicit_local_contributor(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        inventory = {
            "items": [
                {"requirement_id": "criterion:value"},
                {"requirement_id": "criterion:format"},
            ]
        }
        diagnosis = {
            "evidence_status": "confirmed",
            "target_ref": "member_harness.solver.prompt",
            "failed_requirement": "the output value is wrong",
            "discriminating_evidence": "the trace links the decision to the wrong value",
            "evidence_refs": [{"trace_id": "t", "role": "solver", "message_index": 2}],
            "failure_cluster": {
                "failed_checks": ["criterion:value"],
                "observable_behavior": "the value is wrong",
            },
            "causal_coverage": {
                "explained_requirement_ids": ["criterion:value"],
                "residual_requirement_ids": ["criterion:format"],
                "unexplained_observations": ["the format failure has another cause"],
                "causal_chain": [
                    {
                        "cause": "the solver chose the wrong value",
                        "effect": "the value criterion failed",
                        "evidence_status": "observed",
                        "evidence_refs": [],
                    }
                ],
                "counterfactual_prediction": "the value changes while formatting remains unchanged",
                "sufficiency_status": "local_contributor",
            },
            "decision_contract": {"acceptance_observable": "the value criterion improves"},
            "confidence": "high",
        }

        assert (
            _diagnosis_validation_conflicts(
                diagnosis,
                {},
                failed_requirement_inventory=inventory,
            )
            == []
        )

    def test_causal_coverage_rejects_explaining_checks_outside_own_cluster(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _diagnosis_validation_conflicts

        diagnosis = {
            "evidence_status": "supported_hypothesis",
            "target_ref": "member_harness.solver.prompt",
            "failed_requirement": "the value requirement failed",
            "discriminating_evidence": "the trace supports the value-decision gap",
            "evidence_refs": [{"trace_id": "t", "role": "solver", "message_index": 2}],
            "failure_cluster": {
                "failed_checks": ["criterion:value"],
                "observable_behavior": "the value is wrong",
            },
            "causal_coverage": {
                "explained_requirement_ids": ["criterion:value", "criterion:format"],
                "residual_requirement_ids": [],
                "unexplained_observations": [],
                "causal_chain": [
                    {"cause": "wrong value decision", "effect": "value failed", "evidence_status": "supported"}
                ],
                "counterfactual_prediction": "the value changes",
                "sufficiency_status": "cluster_sufficient",
            },
            "decision_contract": {"acceptance_observable": "the value criterion improves"},
            "confidence": "medium",
        }

        conflicts = _diagnosis_validation_conflicts(
            diagnosis,
            {},
            failed_requirement_inventory={
                "items": [
                    {"requirement_id": "criterion:value"},
                    {"requirement_id": "criterion:format"},
                ]
            },
        )

        assert any("must exactly match this diagnosis's failure_cluster" in item for item in conflicts)

    def test_diagnosis_set_cannot_drop_an_independent_failed_requirement(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _case_diagnoses_validation_conflicts,
        )

        inventory = {
            "items": [
                {"requirement_id": "criterion:value"},
                {"requirement_id": "criterion:format"},
            ]
        }
        diagnosis = {
            "failure_cluster": {
                "failed_checks": ["criterion:value"],
                "observable_behavior": "the value is wrong",
            },
            "causal_coverage": {
                "explained_requirement_ids": ["criterion:value"],
                "residual_requirement_ids": ["criterion:format"],
                "unexplained_observations": ["the format failure has another cause"],
                "causal_chain": [
                    {
                        "cause": "wrong decision",
                        "effect": "wrong value",
                        "evidence_status": "supported",
                    }
                ],
                "counterfactual_prediction": "the value changes but formatting does not",
                "sufficiency_status": "local_contributor",
            },
        }

        conflicts = _case_diagnoses_validation_conflicts(
            [diagnosis],
            {},
            failed_requirement_inventory=inventory,
        )

        assert conflicts == ["diagnosis set omitted failed requirement IDs: criterion:format"]

    def test_generic_protocol_rejects_assigned_target_with_insufficient_evidence(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _diagnosis_validation_conflicts,
        )

        conflicts = _diagnosis_validation_conflicts(
            {
                "evidence_status": "insufficient",
                "target_ref": "member_harness.solver.prompt",
                "confidence": "medium",
                "failed_requirement": "unknown",
                "discriminating_evidence": "none",
                "evidence_refs": [{"trace_id": "t", "role": "solver", "message_index": 1}],
                "decision_contract": {"acceptance_observable": "a changed answer"},
            },
            {},
        )

        assert "insufficient evidence must use target_ref=unassigned" in conflicts
        assert "insufficient evidence must use confidence=low" in conflicts

    def test_system_prompt_preserves_role_aware_target_refs(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            DIAGNOSIS_SYSTEM_PROMPT,
        )

        assert "member_harness.<role>.<variable>" in DIAGNOSIS_SYSTEM_PROMPT
        assert "team_skill.<role>.<variable>" in DIAGNOSIS_SYSTEM_PROMPT
        assert "Never output role-less target_ref" in DIAGNOSIS_SYSTEM_PROMPT

    def test_system_prompt_no_longer_allows_rail_attribution(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            AGGREGATION_SYSTEM_PROMPT,
            DIAGNOSIS_SYSTEM_PROMPT,
        )

        assert "rail:" not in DIAGNOSIS_SYSTEM_PROMPT
        assert "Valid member_harness variables: prompt, skill, tool, config." in (AGGREGATION_SYSTEM_PROMPT)
        assert "Valid member_harness variables: prompt, skill, tool, rail, config." not in (AGGREGATION_SYSTEM_PROMPT)

    def test_prepare_evidence_summary_does_not_require_artifacts_dir(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
            _prepare_diagnosis_evidence,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_aggregation_prompt,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        metadata: dict[str, Any] = {"parsed": {"dimensions": {"avg_behavior_score": 0.5}}}

        result = _summarize_evaluation_metadata(metadata)

        assert result == {}

    def test_summarize_evaluation_metadata_returns_empty_for_non_judge_cases(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _summarize_evaluation_metadata,
        )

        assert _summarize_evaluation_metadata({}) == {}
        assert _summarize_evaluation_metadata({"attempt": 1}) == {}

    def test_build_diagnosis_input_json_contains_judge_breakdown_for_llm_judge(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_evidence_summary,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput

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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
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


class TestBoundedMultiDiagnosis:
    @staticmethod
    def _diagnosis(
        *,
        failure_mode: str,
        failed_check: str,
        observable: str,
        target_ref: str = "member_harness.solver.skill",
    ) -> dict[str, Any]:
        return {
            "issue_category": "member_harness",
            "severity": "medium",
            "summary": f"failure in {failed_check}",
            "failure_mode": failure_mode,
            "failure_cluster": {
                "failed_checks": [failed_check],
                "observable_behavior": observable,
            },
            "root_cause": f"root cause for {failed_check}",
            "critical_mistake": f"wrong decision before {failed_check}",
            "general_mechanism": "select the behavior required by the observed contract",
            "target_ref": target_ref,
            "evidence_refs": [],
            "affected_components": ["solver"],
            "recommendation": f"update {target_ref}",
            "decision_contract": {
                "acceptance_observable": observable,
                "activation_phase": "during_investigation",
            },
            "confidence": "medium",
        }

    def test_normalize_case_diagnoses_bounds_deduplicates_and_prioritizes_residuals(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _normalize_case_diagnoses,
        )

        fixed = self._diagnosis(
            failure_mode="fixed_formula",
            failed_check="formula_check",
            observable="formula is recalculated correctly",
        )
        remaining = self._diagnosis(
            failure_mode="missing_payment",
            failed_check="payment_b_check",
            observable="Payment B appears in the output",
        )
        duplicate_remaining = self._diagnosis(
            failure_mode="missing_payment",
            failed_check="payment_b_check",
            observable="Payment B appears in the output",
        )
        unrelated = self._diagnosis(
            failure_mode="wrong_header",
            failed_check="header_check",
            observable="the required header is present",
            target_ref="member_harness.solver.prompt",
        )
        regression = self._diagnosis(
            failure_mode="regressed_total",
            failed_check="existing_total_check",
            observable="the previously correct total remains unchanged",
        )
        parsed = {
            "diagnoses": [
                fixed,
                remaining,
                duplicate_remaining,
                unrelated,
                regression,
            ]
        }
        feedback = {
            "experiments": [
                {
                    "verifier_delta": {
                        "newly_passed_fail_to_pass": ["formula_check"],
                        "remaining_failed_fail_to_pass": ["payment_b_check"],
                        "regressed_pass_to_pass": ["existing_total_check"],
                    }
                }
            ]
        }

        diagnoses = _normalize_case_diagnoses(
            parsed,
            prior_candidate_feedback=feedback,
        )

        assert [item["failure_mode"] for item in diagnoses] == [
            "regressed_total",
            "missing_payment",
            "wrong_header",
        ]
        assert all(item["failure_mode"] != "fixed_formula" for item in diagnoses)

        legacy = _normalize_case_diagnoses(remaining)
        assert legacy == [remaining]

    def test_normalize_case_diagnoses_rejects_empty_json_placeholders(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _normalize_case_diagnoses,
        )

        assert _normalize_case_diagnoses({"diagnoses": [{}]}) == []
        assert _normalize_case_diagnoses({"diagnoses": [{"severity": "medium"}]}) == []
        assert _normalize_case_diagnoses(
            {
                "diagnoses": [
                    {
                        "target_ref": "unassigned",
                        "summary": "Current evidence cannot attribute the failure.",
                    }
                ]
            }
        ) == [
            {
                "target_ref": "unassigned",
                "summary": "Current evidence cannot attribute the failure.",
            }
        ]

    def test_normalize_preserves_supported_issue_and_splits_unresolved_residual(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _normalize_case_diagnoses

        diagnosis = self._diagnosis(
            failure_mode="incomplete_extraction",
            failed_check="criterion:value",
            observable="the extracted value is incomplete",
        )
        diagnosis.update(
            {
                "evidence_status": "confirmed",
                "causal_coverage": {
                    "explained_requirement_ids": ["criterion:value"],
                    "residual_requirement_ids": ["criterion:format"],
                    "unexplained_observations": ["the format mechanism is unresolved"],
                    "causal_chain": [
                        {
                            "cause": "the extraction stopped early",
                            "effect": "the value is incomplete",
                            "evidence_status": "supported",
                        }
                    ],
                    "counterfactual_prediction": "the extracted value becomes complete",
                    "sufficiency_status": "local_contributor",
                },
                "hypothesis_assessment": [
                    {
                        "hypothesis_id": "h_extract",
                        "status": "supported",
                        "falsifying_condition_status": "not_observed",
                        "claim_follows_from_evidence": "yes",
                        "logic_check": "the exact read ends before the required value",
                        "controller_request_ids": ["q1"],
                    },
                    {
                        "hypothesis_id": "h_format",
                        "status": "unresolved",
                        "falsifying_condition_status": "unknown",
                        "claim_follows_from_evidence": "unknown",
                        "logic_check": "the format discriminator is unavailable",
                        "controller_request_ids": [],
                    },
                ],
            }
        )

        normalized = _normalize_case_diagnoses({"diagnoses": [diagnosis]})

        assert len(normalized) == 2
        supported, residual = normalized
        assert supported["target_ref"] == "member_harness.solver.skill"
        assert supported["evidence_status"] == "supported_hypothesis"
        assert supported["causal_coverage"]["sufficiency_status"] == "local_contributor"
        assert [item["hypothesis_id"] for item in supported["hypothesis_assessment"]] == ["h_extract"]
        assert residual["target_ref"] == "unassigned"
        assert residual["evidence_status"] == "insufficient"
        assert residual["causal_coverage"]["explained_requirement_ids"] == []
        assert residual["failure_cluster"]["failed_checks"] == ["criterion:format"]
        assert residual["causal_coverage"]["residual_requirement_ids"] == ["criterion:format"]
        assert [item["hypothesis_id"] for item in residual["hypothesis_assessment"]] == ["h_format"]

    def test_normalize_keeps_supported_local_issue_when_only_an_alternative_is_unresolved(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _normalize_case_diagnoses

        diagnosis = self._diagnosis(
            failure_mode="wrong_decision",
            failed_check="criterion:value",
            observable="the selected value is unsupported",
        )
        diagnosis.update(
            {
                "evidence_status": "supported_hypothesis",
                "hypothesis_assessment": [
                    {"hypothesis_id": "h_observed", "status": "supported"},
                    {"hypothesis_id": "h_alternative", "status": "unresolved"},
                ],
                "causal_coverage": {
                    "explained_requirement_ids": ["criterion:value"],
                    "residual_requirement_ids": [],
                    "unexplained_observations": [],
                    "causal_chain": [
                        {
                            "cause": "an observed decision",
                            "effect": "the selected value is unsupported",
                            "evidence_status": "supported",
                        }
                    ],
                    "counterfactual_prediction": "the selected value follows the observed source",
                    "sufficiency_status": "cluster_sufficient",
                },
            }
        )

        normalized = _normalize_case_diagnoses({"diagnoses": [diagnosis]})

        assert len(normalized) == 1
        assert normalized[0]["target_ref"] == "member_harness.solver.skill"
        assert [item["hypothesis_id"] for item in normalized[0]["hypothesis_assessment"]] == ["h_observed"]
        assert normalized[0]["causal_coverage"]["sufficiency_status"] == "local_contributor"

    def test_causal_reconciliation_downgrades_only_the_unsupported_hypothesis(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="wrong_decision",
            failed_check="criterion:value",
            observable="the selected value violates the source",
        )
        diagnosis["evidence_status"] = "confirmed"
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_supported",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q1 contains the source value used by the agent",
                "controller_request_ids": ["q1"],
            },
            {
                "hypothesis_id": "h_bad",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "logic_check": "q2 supports the alternative",
                "controller_request_ids": ["q2"],
            },
        ]
        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {"hypothesis_id": "h_supported", "claim": "observed wrong decision"},
                    {"hypothesis_id": "h_bad", "claim": "unobserved alternative"},
                ],
                "evidence_requests": [
                    {"request_id": "q1", "hypothesis_ids": ["h_supported"], "operation": "read_artifact_window"},
                    {"request_id": "q2", "hypothesis_ids": ["h_bad"], "operation": "inspect_artifact"},
                ],
            },
            evidence_results={
                "results": [
                    {"request_id": "q1", "availability": "available"},
                    {"request_id": "q2", "availability": "not_available"},
                ]
            },
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
        )

        assert reconciled[0]["target_ref"] == "member_harness.solver.skill"
        assert reconciled[0]["evidence_status"] == "supported_hypothesis"
        statuses = {item["hypothesis_id"]: item["status"] for item in reconciled[0]["hypothesis_assessment"]}
        assert statuses == {"h_supported": "supported", "h_bad": "unresolved"}
        assert any("downgraded h_bad" in warning for warning in warnings)

    def test_causal_reconciliation_preserves_paired_experiment_when_model_omits_assessment(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _normalize_prior_experiment_assessment,
            _reconcile_causal_assessments,
        )

        diagnosis = self._diagnosis(
            failure_mode="wrong_order",
            failed_check="criterion:value",
            observable="the downstream operation used unchanged source state",
        )
        diagnosis["evidence_status"] = "supported_hypothesis"
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_order",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "logic_check": "q1 shows the downstream operation preceded the source mutation",
                "controller_request_ids": ["q1"],
            }
        ]

        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [{"hypothesis_id": "h_order", "claim": "the causal order was reversed"}],
                "evidence_requests": [{"request_id": "q1", "hypothesis_ids": ["h_order"], "operation": "search_trace"}],
            },
            evidence_results={"results": [{"request_id": "q1", "availability": "available"}]},
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
            prior_candidate_feedback={
                "experiments": [
                    {
                        "activation": {"state": "unknown"},
                        "target_score_delta": 0.0,
                        "causal_intervention_contracts": [{"source_causal_hypothesis_id": "h_previous"}],
                    }
                ]
            },
        )

        assessment = reconciled[0]["prior_experiment_assessment"]
        assert assessment["availability"] == "available"
        assert assessment["intervention_activated"] == "unknown"
        assert assessment["predicted_outcome_occurred"] == "no"
        assert assessment["causal_hypothesis_status"] == "inconclusive"
        assert any("synthesized a conservative paired-experiment" in warning for warning in warnings)

        corrected, correction = _normalize_prior_experiment_assessment(
            {
                "availability": "available",
                "intervention_activated": "no",
                "predicted_behavior_occurred": "no",
                "predicted_outcome_occurred": "no",
                "causal_hypothesis_status": "falsified",
                "reason": "The score did not improve.",
            }
        )
        assert corrected["causal_hypothesis_status"] == "not_tested"
        assert "did not activate" in correction

    def test_causal_reconciliation_rejects_form_only_activation_when_failure_persists(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="unverified_decision_ground_used",
            failed_check="criterion:value",
            observable="the released decision still uses a ground with an incomplete requirement chain",
        )
        diagnosis["evidence_status"] = "supported_hypothesis"
        diagnosis["prior_experiment_assessment"] = {
            "availability": "available",
            "intervention_activated": "yes",
            "predicted_behavior_occurred": "yes",
            "predicted_outcome_occurred": "no",
            "causal_hypothesis_status": "falsified",
            "reason": "A verification table was visible, but the score did not improve.",
        }
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_ground",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q1 shows the incomplete ground was retained",
                "controller_request_ids": ["q1"],
            }
        ]
        feedback = {
            "experiments": [
                {
                    "causal_intervention_contracts": [
                        {"source_causal_hypothesis_semantic_id": ("chs:unverified_decision_ground_used")}
                    ]
                }
            ]
        }

        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [{"hypothesis_id": "h_ground", "claim": "an unverified ground was used"}],
                "evidence_requests": [{"request_id": "q1", "hypothesis_ids": ["h_ground"], "operation": "read_event"}],
            },
            evidence_results={"results": [{"request_id": "q1", "availability": "available"}]},
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
            prior_candidate_feedback=feedback,
        )

        assessment = reconciled[0]["prior_experiment_assessment"]
        assert assessment["intervention_activated"] == "yes"
        assert assessment["predicted_behavior_occurred"] == "no"
        assert assessment["predicted_outcome_occurred"] == "no"
        assert assessment["causal_hypothesis_status"] == "not_tested"
        assert "visible form may have appeared" in assessment["reason"]
        assert any("pre-registered failure mechanism remained supported" in warning for warning in warnings)

    def test_causal_reconciliation_preserves_valid_cluster_when_sibling_uses_unknown_id(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        supported = self._diagnosis(
            failure_mode="wrong_decision",
            failed_check="criterion:value",
            observable="the selected value violates the source",
        )
        supported["evidence_status"] = "confirmed"
        supported["causal_coverage"] = {
            "explained_requirement_ids": ["criterion:value"],
            "residual_requirement_ids": [],
            "unexplained_observations": [],
            "causal_chain": [
                {
                    "cause": "the proven conversion was not reused",
                    "effect": "the final artifact retained the old value",
                    "evidence_status": "observed",
                }
            ],
            "counterfactual_prediction": "reusing the proven conversion persists the selected value",
            "sufficiency_status": "task_sufficient",
        }
        supported["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_supported",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q1 shows the successful method was not reused",
                "controller_request_ids": ["q1"],
            }
        ]
        malformed_sibling = self._diagnosis(
            failure_mode="redundant_story",
            failed_check="criterion:typo_value",
            observable="a second narrative repeats the same failed output",
        )
        malformed_sibling["evidence_status"] = "supported_hypothesis"
        malformed_sibling["causal_coverage"] = {
            "explained_requirement_ids": ["criterion:typo_value"],
            "residual_requirement_ids": [],
            "unexplained_observations": [],
            "causal_chain": [
                {
                    "cause": "an unsupported alternative",
                    "effect": "the same output failed",
                    "evidence_status": "supported",
                }
            ],
            "counterfactual_prediction": "the alternative would change the output",
            "sufficiency_status": "cluster_sufficient",
        }
        malformed_sibling["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_alternative",
                "status": "unresolved",
                "falsifying_condition_status": "unknown",
                "claim_follows_from_evidence": "unknown",
                "logic_check": "the alternative remains unresolved",
                "controller_request_ids": [],
            }
        ]

        reconciled, warnings = _reconcile_causal_assessments(
            [supported, malformed_sibling],
            {
                "hypotheses": [
                    {"hypothesis_id": "h_supported", "claim": "the proven method was not reused"},
                    {"hypothesis_id": "h_alternative", "claim": "an alternative mechanism occurred"},
                ],
                "evidence_requests": [
                    {"request_id": "q1", "hypothesis_ids": ["h_supported"], "operation": "read_event"},
                    {"request_id": "q2", "hypothesis_ids": ["h_alternative"], "operation": "search_trace"},
                ],
            },
            evidence_results={
                "results": [
                    {"request_id": "q1", "availability": "available"},
                    {"request_id": "q2", "availability": "not_found"},
                ]
            },
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
        )

        assert len(reconciled) == 1
        assert reconciled[0]["target_ref"] == "member_harness.solver.skill"
        statuses = {item["hypothesis_id"]: item["status"] for item in reconciled[0]["hypothesis_assessment"]}
        assert statuses == {"h_supported": "supported", "h_alternative": "unresolved"}
        assert any("dropped a redundant cluster" in warning for warning in warnings)

    def test_causal_refinement_admits_only_independently_tested_new_hypothesis(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _merge_causal_investigation,
            _normalize_causal_refinement,
        )

        base = {
            "hypotheses": [
                {
                    "hypothesis_id": "h_initial",
                    "claim": "The conversion command failed.",
                    "explains_requirement_ids": ["criterion:artifact"],
                    "current_support": [],
                    "falsified_if": "The conversion command succeeded.",
                    "numeric_change_check_required": True,
                }
            ],
            "evidence_requests": [
                {
                    "request_id": "q1",
                    "hypothesis_ids": ["h_initial"],
                    "operation": "search_trace",
                    "query": "conversion succeeded",
                }
            ],
        }
        refinement = _normalize_causal_refinement(
            {
                "investigation": {
                    "hypotheses": [
                        {
                            "hypothesis_id": "h_persist",
                            "claim": "A successful intermediate artifact was not persisted as the deliverable.",
                            "explains_requirement_ids": ["criterion:artifact"],
                            "current_support": ["q1 revealed a successful conversion"],
                            "falsified_if": "The converted artifact was copied to the final deliverable path.",
                            "origin": "abductive_refinement",
                            "discovery_evidence_request_ids": ["q1"],
                            "evidence_requests": [
                                {
                                    "request_id": "q_persist",
                                    "operation": "search_trace",
                                    "query": "copy final deliverable converted artifact",
                                }
                            ],
                        }
                    ]
                }
            },
            base=base,
            failed_requirement_ids=["criterion:artifact"],
        )

        assert refinement is not None
        hypotheses = {item["hypothesis_id"]: item for item in refinement["hypotheses"]}
        assert hypotheses["h_initial"]["numeric_change_check_required"] is True
        assert hypotheses["h_persist"]["origin"] == "abductive_refinement"
        assert hypotheses["h_persist"]["discovery_evidence_request_ids"] == ["q1"]
        merged, additions = _merge_causal_investigation(base, refinement)
        assert [item["hypothesis_id"] for item in merged["hypotheses"]] == ["h_initial", "h_persist"]
        assert len(additions) == 1
        assert additions[0]["hypothesis_ids"] == ["h_persist"]

    def test_causal_refinement_rejects_discovered_story_without_new_test(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _merge_causal_investigation,
            _normalize_causal_refinement,
        )

        base = {
            "hypotheses": [
                {
                    "hypothesis_id": "h_initial",
                    "claim": "The parser failed.",
                    "explains_requirement_ids": ["criterion:value"],
                    "falsified_if": "The parser succeeded.",
                    "numeric_change_check_required": False,
                }
            ],
            "evidence_requests": [
                {
                    "request_id": "q1",
                    "hypothesis_ids": ["h_initial"],
                    "operation": "search_trace",
                    "query": "parser succeeded",
                }
            ],
        }
        refinement = _normalize_causal_refinement(
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_story",
                        "claim": "A different mechanism caused the failure.",
                        "explains_requirement_ids": ["criterion:value"],
                        "falsified_if": "The different mechanism did not occur.",
                        "origin": "abductive_refinement",
                        "discovery_evidence_request_ids": ["q1"],
                        "evidence_requests": [],
                    }
                ]
            },
            base=base,
            failed_requirement_ids=["criterion:value"],
        )

        assert refinement is not None
        merged, additions = _merge_causal_investigation(base, refinement)
        assert [item["hypothesis_id"] for item in merged["hypotheses"]] == ["h_initial"]
        assert additions == []

    def test_investigation_diagnosis_prompt_requires_cluster_wide_falsifier_matrix(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_investigation_diagnosis_prompt,
        )

        prompt = _build_investigation_diagnosis_prompt(
            original_prompt="diagnose",
            investigation={"hypotheses": [], "evidence_requests": []},
            evidence_results={"results": []},
        )

        assert "internal falsifier matrix" in prompt
        assert "does not give one hypothesis exclusive ownership" in prompt
        assert "Test every necessary subclaim in a composite explanation" in prompt
        assert "another hypothesis's evidence-backed" in prompt
        assert "`current_support`" in prompt

    def test_compatible_evidence_requests_share_facts_only_inside_requirement_cluster(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _compatible_evidence_requests,
        )

        compatible = _compatible_evidence_requests(
            {
                "hypotheses": [
                    {"hypothesis_id": "h1", "explains_requirement_ids": ["criterion:value"]},
                    {"hypothesis_id": "h2", "explains_requirement_ids": ["criterion:value"]},
                    {"hypothesis_id": "h3", "explains_requirement_ids": ["criterion:format"]},
                ],
                "evidence_requests": [
                    {"request_id": "q2", "hypothesis_ids": ["h2"]},
                    {"request_id": "q3", "hypothesis_ids": ["h3"]},
                ],
            }
        )

        assert compatible["h1"] == {"q2"}
        assert compatible["h2"] == {"q2"}
        assert compatible["h3"] == {"q3"}

    def test_causal_reconciliation_does_not_borrow_unrelated_hypothesis_evidence(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="wrong_decision",
            failed_check="criterion:value",
            observable="the selected value is wrong",
        )
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_read",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "logic_check": "q_route was available",
                "controller_request_ids": ["q_route"],
            },
            {
                "hypothesis_id": "h_route",
                "status": "unresolved",
                "falsifying_condition_status": "unknown",
                "claim_follows_from_evidence": "unknown",
                "logic_check": "routing remains unresolved",
                "controller_request_ids": [],
            },
        ]
        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {"hypothesis_id": "h_read", "claim": "The source read stopped early."},
                    {"hypothesis_id": "h_route", "claim": "Routing selected the wrong source."},
                ],
                "evidence_requests": [
                    {"request_id": "q_read", "hypothesis_ids": ["h_read"], "operation": "read_event"},
                    {"request_id": "q_route", "hypothesis_ids": ["h_route"], "operation": "read_event"},
                ],
            },
            evidence_results={
                "results": [
                    {"request_id": "q_read", "availability": "not_found"},
                    {"request_id": "q_route", "availability": "available"},
                ]
            },
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
        )

        statuses = {item["hypothesis_id"]: item["status"] for item in reconciled[0]["hypothesis_assessment"]}
        assert statuses["h_read"] == "unresolved"
        assert any("stripped evidence outside h_read" in warning for warning in warnings)

    def test_causal_reconciliation_shares_discriminator_with_competing_hypothesis(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="wrong_state",
            failed_check="criterion:value",
            observable="the downstream value used unchanged source state",
        )
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_skipped",
                "status": "unresolved",
                "falsifying_condition_status": "unknown",
                "claim_follows_from_evidence": "unknown",
                "logic_check": "the source-state inspection is inconclusive for this alternative",
                "controller_request_ids": [],
            },
            {
                "hypothesis_id": "h_stale",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q_state shows the source changed while the derived value stayed stale",
                "controller_request_ids": ["q_state"],
            },
        ]
        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_skipped",
                        "claim": "the source mutation was skipped",
                        "explains_requirement_ids": ["criterion:value"],
                    },
                    {
                        "hypothesis_id": "h_stale",
                        "claim": "the source changed but the derived state stayed stale",
                        "explains_requirement_ids": ["criterion:value"],
                    },
                ],
                "evidence_requests": [
                    {
                        "request_id": "q_state",
                        "hypothesis_ids": ["h_skipped"],
                        "operation": "read_artifact_window",
                    }
                ],
            },
            evidence_results={"results": [{"request_id": "q_state", "availability": "available"}]},
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
        )

        statuses = {item["hypothesis_id"]: item["status"] for item in reconciled[0]["hypothesis_assessment"]}
        assert statuses["h_stale"] == "supported"
        assert not any("stripped evidence outside h_stale" in warning for warning in warnings)

    def test_causal_reconciliation_keeps_valid_scoped_evidence_when_extra_citation_is_stripped(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="proven_method_not_reused",
            failed_check="criterion:value",
            observable="the final artifact retained the old value",
        )
        diagnosis["evidence_status"] = "confirmed"
        diagnosis["causal_coverage"] = {
            "explained_requirement_ids": ["criterion:value"],
            "residual_requirement_ids": [],
            "unexplained_observations": [],
            "causal_chain": [
                {
                    "cause": "the proven method was not reused",
                    "effect": "the final artifact retained the old value",
                    "evidence_status": "observed",
                }
            ],
            "counterfactual_prediction": "reusing the method updates the final artifact",
            "sufficiency_status": "task_sufficient",
        }
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_reuse",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q_reuse shows the working method was not applied to the final artifact",
                "controller_request_ids": ["q_reuse", "q_other"],
            }
        ]

        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_reuse",
                        "claim": "the proven method was not reused",
                        "explains_requirement_ids": ["criterion:value"],
                    },
                    {"hypothesis_id": "h_other", "claim": "another mechanism occurred"},
                ],
                "evidence_requests": [
                    {"request_id": "q_reuse", "hypothesis_ids": ["h_reuse"], "operation": "read_event"},
                    {"request_id": "q_other", "hypothesis_ids": ["h_other"], "operation": "search_trace"},
                ],
            },
            evidence_results={
                "results": [
                    {"request_id": "q_reuse", "availability": "available"},
                    {"request_id": "q_other", "availability": "available"},
                ]
            },
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
        )

        assessment = reconciled[0]["hypothesis_assessment"][0]
        assert assessment["status"] == "supported"
        assert assessment["controller_request_ids"] == ["q_reuse"]
        assert reconciled[0]["target_ref"] == "member_harness.solver.skill"
        assert any("stripped evidence outside h_reuse" in warning for warning in warnings)

    def test_causal_reconciliation_repairs_residual_coverage_for_already_unassigned_diagnosis(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="unresolved_mechanism",
            failed_check="criterion:value",
            observable="the authoritative value remains unmet",
            target_ref="unassigned",
        )
        diagnosis["evidence_status"] = "insufficient"
        diagnosis["causal_coverage"] = {
            "explained_requirement_ids": ["criterion:value"],
            "residual_requirement_ids": [],
            "unexplained_observations": [],
            "causal_chain": [
                {
                    "cause": "unknown mechanism",
                    "effect": "the value remains unmet",
                    "evidence_status": "unknown",
                }
            ],
            "counterfactual_prediction": "no change is predicted without a discriminator",
            "sufficiency_status": "unknown",
        }
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_unknown",
                "status": "unresolved",
                "falsifying_condition_status": "unknown",
                "claim_follows_from_evidence": "unknown",
                "logic_check": "the available request did not distinguish the mechanism",
                "controller_request_ids": [],
            }
        ]

        reconciled, _ = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_unknown",
                        "claim": "an unresolved mechanism occurred",
                        "explains_requirement_ids": ["criterion:value"],
                    }
                ],
                "evidence_requests": [
                    {"request_id": "q1", "hypothesis_ids": ["h_unknown"], "operation": "search_trace"}
                ],
            },
            evidence_results={"results": [{"request_id": "q1", "availability": "not_found"}]},
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
        )

        assert reconciled[0]["evidence_status"] == "insufficient"
        assert reconciled[0]["causal_coverage"]["explained_requirement_ids"] == []
        assert reconciled[0]["causal_coverage"]["residual_requirement_ids"] == ["criterion:value"]

    def test_causal_reconciliation_drops_redundant_unresolved_alternative(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _case_diagnoses_validation_conflicts,
            _reconcile_causal_assessments,
        )

        supported = self._diagnosis(
            failure_mode="supported_decision_failure",
            failed_check="criterion:value",
            observable="the wrong branch was selected",
        )
        supported["evidence_status"] = "confirmed"
        supported["evidence_refs"] = [{"trace_id": "trace-1", "message_index": 3}]
        supported["failed_requirement"] = "criterion:value must be satisfied"
        supported["discriminating_evidence"] = "q1 directly distinguishes the selected branch"
        supported["causal_coverage"] = {
            "explained_requirement_ids": ["criterion:value"],
            "residual_requirement_ids": [],
            "unexplained_observations": [],
            "causal_chain": [
                {
                    "cause": "the wrong branch was selected",
                    "effect": "the required value was omitted",
                    "evidence_status": "observed",
                }
            ],
            "counterfactual_prediction": "selecting the supported branch emits the required value",
            "sufficiency_status": "task_sufficient",
        }
        supported["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_supported",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q1 directly shows the wrong branch decision",
                "controller_request_ids": ["q1"],
            }
        ]
        unresolved = self._diagnosis(
            failure_mode="unresolved_alternative",
            failed_check="criterion:value",
            observable="the same failed value may have another cause",
            target_ref="unassigned",
        )
        unresolved["evidence_status"] = "insufficient"
        unresolved["issue_category"] = "unassigned"
        unresolved["confidence"] = "low"
        unresolved["evidence_refs"] = []
        unresolved["causal_coverage"] = {
            "explained_requirement_ids": [],
            "residual_requirement_ids": ["criterion:value"],
            "unexplained_observations": ["the alternative remains unresolved"],
            "causal_chain": [
                {
                    "cause": "an unknown alternative",
                    "effect": "the required value was omitted",
                    "evidence_status": "unknown",
                }
            ],
            "counterfactual_prediction": "no change is predicted without a discriminator",
            "sufficiency_status": "unknown",
        }
        unresolved["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_unresolved",
                "status": "unresolved",
                "falsifying_condition_status": "unknown",
                "claim_follows_from_evidence": "unknown",
                "logic_check": "no discriminator is available",
                "controller_request_ids": [],
            }
        ]
        investigation = {
            "hypotheses": [
                {"hypothesis_id": "h_supported", "explains_requirement_ids": ["criterion:value"]},
                {"hypothesis_id": "h_unresolved", "explains_requirement_ids": ["criterion:value"]},
            ],
            "evidence_requests": [{"request_id": "q1", "hypothesis_ids": ["h_supported"], "operation": "read_event"}],
        }
        failed_inventory = {"items": [{"requirement_id": "criterion:value"}]}

        reconciled, warnings = _reconcile_causal_assessments(
            [supported, unresolved],
            investigation,
            evidence_results={"results": [{"request_id": "q1", "availability": "available"}]},
            failed_requirement_inventory=failed_inventory,
        )

        assert len(reconciled) == 1
        assert reconciled[0]["failure_mode"] == "supported_decision_failure"
        assert any("redundant unresolved alternative" in warning for warning in warnings)
        assert not _case_diagnoses_validation_conflicts(
            reconciled,
            {"project_test_events": []},
            failed_requirement_inventory=failed_inventory,
        )

    def test_causal_reconciliation_isolates_outcome_dependent_handoff_and_preserves_ledger(
        self,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _causal_investigation_conflicts,
            _reconcile_causal_assessments,
        )

        invalid = self._diagnosis(
            failure_mode="outcome_fitted_action",
            failed_check="criterion:value",
            observable="the released value violates the public contract",
        )
        invalid["evidence_status"] = "supported_hypothesis"
        invalid["selected_hypothesis_id"] = "h_observed"
        invalid["root_cause"] = "The runtime should choose the evaluator's expected answer."
        invalid["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_observed",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q1 shows which branch the runtime selected",
                "controller_request_ids": ["q1"],
            }
        ]
        valid = self._diagnosis(
            failure_mode="post_mutation_validation_omitted",
            failed_check="criterion:value",
            observable="the exact released object was not checked after its last mutation",
        )
        valid["evidence_status"] = "confirmed"
        valid["selected_hypothesis_id"] = "h_validation"
        valid["root_cause"] = "The final mutation was followed by release rather than validation."
        valid["recommendation"] = "Validate the exact released object after its last mutation."
        valid["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_validation",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q2 shows mutation followed directly by release",
                "controller_request_ids": ["q2"],
                "handoff_disposition": "selected",
                "handoff_reason": "This diagnosis selects the observed validation omission.",
            }
        ]
        investigation = {
            "hypotheses": [
                {
                    "hypothesis_id": "h_observed",
                    "claim": "The runtime selected the observed branch.",
                    "falsified_if": "The trace shows a different branch.",
                    "explains_requirement_ids": ["criterion:value"],
                },
                {
                    "hypothesis_id": "h_validation",
                    "claim": "The exact released object was not validated after its last mutation.",
                    "falsified_if": "A post-mutation validation of that object is present.",
                    "explains_requirement_ids": ["criterion:value"],
                },
                {
                    "hypothesis_id": "h_alternative",
                    "claim": "A different production mechanism caused the invalid value.",
                    "falsified_if": "The production state is proven valid before release.",
                    "explains_requirement_ids": ["criterion:value"],
                },
            ],
            "evidence_requests": [
                {"request_id": "q1", "hypothesis_ids": ["h_observed"], "operation": "read_event"},
                {"request_id": "q2", "hypothesis_ids": ["h_validation"], "operation": "read_event"},
                {"request_id": "q3", "hypothesis_ids": ["h_alternative"], "operation": "read_event"},
            ],
        }
        evidence_results = {
            "results": [
                {"request_id": "q1", "availability": "available"},
                {"request_id": "q2", "availability": "available"},
                {"request_id": "q3", "availability": "not_found"},
            ]
        }
        failed_inventory = {"items": [{"requirement_id": "criterion:value"}]}

        reconciled, warnings = _reconcile_causal_assessments(
            [invalid, valid],
            investigation,
            evidence_results=evidence_results,
            failed_requirement_inventory=failed_inventory,
        )

        assert len(reconciled) == 1
        assert reconciled[0]["target_ref"] == "member_harness.solver.skill"
        assert reconciled[0]["selected_hypothesis_id"] == "h_validation"
        assessments = {item["hypothesis_id"]: item for item in reconciled[0]["hypothesis_assessment"]}
        assert set(assessments) == {"h_observed", "h_validation", "h_alternative"}
        assert assessments["h_observed"]["handoff_disposition"] == "non_actionable"
        assert assessments["h_alternative"]["status"] == "unresolved"
        assert reconciled[0]["evidence_status"] == "supported_hypothesis"
        assert any("evaluator-outcome-dependent" in warning for warning in warnings)
        assert not _causal_investigation_conflicts(
            reconciled,
            investigation,
            evidence_results=evidence_results,
            prior_candidate_feedback=None,
        )

    def test_causal_reconciliation_keeps_structural_support_with_numeric_context(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="artifact_update_not_persisted",
            failed_check="criterion:scenario_value",
            observable="the persisted artifact still contains the source state",
        )
        diagnosis["evidence_status"] = "confirmed"
        diagnosis["causal_coverage"] = {
            "explained_requirement_ids": ["criterion:scenario_value"],
            "residual_requirement_ids": [],
            "unexplained_observations": [],
            "causal_chain": [],
            "counterfactual_prediction": "persisting the update changes the artifact state",
            "sufficiency_status": "cluster_sufficient",
        }
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_write",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q1 shows the update existed only in memory and no artifact write occurred",
                "controller_request_ids": ["q1"],
            }
        ]

        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_write",
                        "claim": "The +1pp update was not persisted to the artifact.",
                        "numeric_change_check_required": False,
                    }
                ],
                "evidence_requests": [
                    {
                        "request_id": "q1",
                        "hypothesis_ids": ["h_write"],
                        "operation": "read_event",
                    }
                ],
            },
            evidence_results={"results": [{"request_id": "q1", "availability": "available"}]},
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:scenario_value"}]},
        )

        assert warnings == []
        assert reconciled[0]["target_ref"] == "member_harness.solver.skill"
        assert reconciled[0]["evidence_status"] == "confirmed"
        assert reconciled[0]["hypothesis_assessment"][0]["status"] == "supported"

    def test_causal_reconciliation_rejects_expected_label_as_root_cause(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="expected_verdict_mismatch",
            failed_check="criterion:verdict",
            observable="the response verdict differs from the scored label",
        )
        diagnosis["evidence_status"] = "supported_hypothesis"
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_label",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "logic_check": "The evaluator expects answer Yes and the candidate that said Yes passed.",
                "controller_request_ids": ["q1"],
            }
        ]

        reconciled, warnings = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_label",
                        "claim": "The evaluator requires the expected answer Yes.",
                    }
                ],
                "evidence_requests": [
                    {
                        "request_id": "q1",
                        "hypothesis_ids": ["h_label"],
                        "operation": "search_trace",
                    }
                ],
            },
            evidence_results={"results": [{"request_id": "q1", "availability": "available"}]},
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:verdict"}]},
        )

        assert reconciled[0]["target_ref"] == "unassigned"
        assert reconciled[0]["evidence_status"] == "insufficient"
        assert reconciled[0]["hypothesis_assessment"][0]["status"] == "unresolved"
        assert any("outcome_reverse_engineering_is_not_causal_evidence" in warning for warning in warnings)

    def test_causal_reconciliation_rejects_handoff_not_bound_to_one_supported_hypothesis(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _reconcile_causal_assessments

        diagnosis = self._diagnosis(
            failure_mode="post_hoc_mechanism",
            failed_check="criterion:value",
            observable="the candidate value remained wrong",
        )
        diagnosis["evidence_status"] = "supported_hypothesis"
        diagnosis["root_cause"] = "The unresolved third mechanism should have been used."
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": "h_observed",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q1 shows the observed mutation",
                "controller_request_ids": ["q1"],
            },
            {
                "hypothesis_id": "h_runtime",
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "evidence_relation": "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "q2 shows the runtime mismatch",
                "controller_request_ids": ["q2"],
            },
            {
                "hypothesis_id": "h_unresolved_action",
                "status": "unresolved",
                "falsifying_condition_status": "unknown",
                "claim_follows_from_evidence": "unknown",
                "logic_check": "the action discriminator was not run",
                "controller_request_ids": [],
            },
        ]

        reconciled, _ = _reconcile_causal_assessments(
            [diagnosis],
            {
                "hypotheses": [
                    {"hypothesis_id": "h_observed", "claim": "an observed mutation occurred"},
                    {"hypothesis_id": "h_runtime", "claim": "a runtime mismatch occurred"},
                    {
                        "hypothesis_id": "h_unresolved_action",
                        "claim": "a different action would satisfy the requirement",
                    },
                ],
                "evidence_requests": [
                    {"request_id": "q1", "hypothesis_ids": ["h_observed"], "operation": "read_event"},
                    {"request_id": "q2", "hypothesis_ids": ["h_runtime"], "operation": "read_artifact_window"},
                ],
            },
            evidence_results={
                "results": [
                    {"request_id": "q1", "availability": "available"},
                    {"request_id": "q2", "availability": "available"},
                ]
            },
            failed_requirement_inventory={"items": [{"requirement_id": "criterion:value"}]},
        )

        assert reconciled[0]["target_ref"] == "unassigned"
        assert reconciled[0]["selected_hypothesis_id"] == ""
        assert reconciled[0]["evidence_status"] == "insufficient"
        assert "not bound to exactly one supported" in " ".join(
            reconciled[0]["causal_coverage"]["unexplained_observations"]
        )

    def test_causal_handoff_audit_requires_exact_selected_hypothesis_and_fails_closed(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _causal_handoff_audit_approved,
            _downgrade_rejected_causal_handoffs,
            _normalize_causal_handoff_audit,
        )

        diagnosis = self._diagnosis(
            failure_mode="wrong_decision",
            failed_check="criterion:value",
            observable="the selected action violated the public contract",
        )
        diagnosis["selected_hypothesis_id"] = "h_contract"
        audit = _normalize_causal_handoff_audit(
            {
                "diagnosis_audits": [
                    {
                        "diagnosis_index": 1,
                        "selected_hypothesis_id": "h_contract",
                        "hypothesis_binding": True,
                        "runtime_decidable": False,
                        "public_contract_consistent": False,
                        "decision_rule_entailed": False,
                        "decision_rule_source": "none",
                        "decision_rule_evidence": "",
                        "evaluation_independent": False,
                        "single_intervention": True,
                        "approved": False,
                        "violations": ["the action depends on an evaluator-owned expected value"],
                    }
                ]
            },
            diagnoses=[diagnosis],
        )

        assert not _causal_handoff_audit_approved(audit)
        downgraded = _downgrade_rejected_causal_handoffs(
            [diagnosis],
            rejected_indices={1},
            violations_by_index={1: audit["diagnosis_audits"][0]["violations"]},
        )
        assert downgraded[0]["target_ref"] == "unassigned"
        assert downgraded[0]["evidence_status"] == "insufficient"
        assert downgraded[0]["causal_coverage"]["explained_requirement_ids"] == []
        assert downgraded[0]["causal_coverage"]["residual_requirement_ids"] == ["criterion:value"]

    def test_causal_handoff_audit_marks_only_omitted_diagnosis_rejected(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _causal_handoff_audit_approved,
            _normalize_causal_handoff_audit,
            _replace_rejected_causal_handoffs,
        )

        first = self._diagnosis(
            failure_mode="first_failure",
            failed_check="criterion:first",
            observable="first observed failure",
        )
        first["selected_hypothesis_id"] = "h1"
        second = self._diagnosis(
            failure_mode="second_failure",
            failed_check="criterion:second",
            observable="second observed failure",
        )
        second["selected_hypothesis_id"] = "h2"

        audit = _normalize_causal_handoff_audit(
            {
                "diagnosis_audits": [
                    {
                        "diagnosis_index": 1,
                        "selected_hypothesis_id": "h1",
                        "hypothesis_binding": True,
                        "runtime_decidable": True,
                        "public_contract_consistent": True,
                        "decision_rule_entailed": True,
                        "decision_rule_source": "runtime_safety_invariant",
                        "decision_rule_evidence": ("Validate the exact released object after its final mutation."),
                        "evaluation_independent": True,
                        "single_intervention": True,
                        "approved": True,
                        "violations": [],
                    }
                ]
            },
            diagnoses=[first, second],
        )

        assert not _causal_handoff_audit_approved(audit)
        assert audit["diagnosis_audits"][0]["approved"] is True
        assert audit["diagnosis_audits"][1]["approved"] is False
        assert "omitted" in audit["diagnosis_audits"][1]["violations"][0]
        replacement = self._diagnosis(
            failure_mode="repaired_second_failure",
            failed_check="criterion:second",
            observable="second failure is now residual",
            target_ref="unassigned",
        )
        merged = _replace_rejected_causal_handoffs(
            [first, second],
            rejected_indices={2},
            replacements=[replacement],
        )
        assert merged[0]["failure_mode"] == "first_failure"
        assert merged[0]["selected_hypothesis_id"] == "h1"
        assert merged[1]["failure_mode"] == "repaired_second_failure"

    def test_causal_handoff_audit_rejects_rule_that_is_only_contract_compatible(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_causal_handoff_audit_prompt,
            _causal_handoff_audit_approved,
            _normalize_causal_handoff_audit,
        )

        diagnosis = self._diagnosis(
            failure_mode="speculative_interpretation",
            failed_check="criterion:value",
            observable="the output does not satisfy the explicit task clause",
        )
        diagnosis["selected_hypothesis_id"] = "h1"
        audit = _normalize_causal_handoff_audit(
            {
                "diagnosis_audits": [
                    {
                        "diagnosis_index": 1,
                        "selected_hypothesis_id": "h1",
                        "hypothesis_binding": True,
                        "runtime_decidable": True,
                        "public_contract_consistent": True,
                        "decision_rule_entailed": False,
                        "decision_rule_source": "none",
                        "decision_rule_evidence": "",
                        "evaluation_independent": True,
                        "single_intervention": True,
                        "approved": False,
                        "violations": ["The proposed action is merely compatible with one possible interpretation."],
                    }
                ]
            },
            diagnoses=[diagnosis],
        )

        assert not _causal_handoff_audit_approved(audit)
        prompt = _build_causal_handoff_audit_prompt(
            public_task_contract="Keep the value constant in all later periods.",
            diagnoses=[diagnosis],
            investigation={"hypotheses": [{"hypothesis_id": "h1", "claim": "a visible decision occurred"}]},
            evidence_results={"results": []},
        )
        assert "decision_rule_entailed is stricter than consistency" in prompt
        assert '"May mean", "could mean", "perhaps intended"' in prompt

    def test_rejected_handoff_with_missing_authority_returns_to_evidence_acquisition(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_causal_handoff_evidence_prompt,
            _causal_handoff_audit_needs_evidence,
        )

        audit = {
            "diagnosis_audits": [
                {
                    "diagnosis_index": 1,
                    "approved": False,
                    "runtime_decidable": False,
                    "decision_rule_entailed": False,
                    "decision_rule_source": "none",
                    "violations": ["No task-visible authority establishes the decision rule."],
                }
            ]
        }

        assert _causal_handoff_audit_needs_evidence(audit)
        prompt = _build_causal_handoff_evidence_prompt(
            public_task_contract="Use the public files to assess the requested outcome.",
            investigation={
                "hypotheses": [{"hypothesis_id": "h1", "claim": "the decision used the wrong authority"}],
                "evidence_requests": [],
            },
            evidence_results={"results": []},
            diagnoses=[{"selected_hypothesis_id": "h1"}],
            audit=audit,
        )

        assert "Do not repair the diagnosis to unassigned yet" in prompt
        assert "sources it named," in prompt
        assert "opened, cited, relied on" in prompt
        assert "use inspect_artifact/read_artifact_window" in prompt
        assert "expected answer" in prompt

    def test_rejected_handoff_without_source_gap_does_not_request_more_evidence(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _causal_handoff_audit_needs_evidence,
        )

        audit = {
            "diagnosis_audits": [
                {
                    "diagnosis_index": 1,
                    "approved": False,
                    "runtime_decidable": True,
                    "decision_rule_entailed": True,
                    "decision_rule_source": "public_task_contract",
                    "violations": ["The diagnosis bundled two independent interventions."],
                }
            ]
        }

        assert not _causal_handoff_audit_needs_evidence(audit)

    def test_evaluator_owned_outcome_cannot_define_causal_hypothesis(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_causal_plan_correction_prompt,
            _causal_plan_outcome_dependency_conflicts,
        )

        conflicts = _causal_plan_outcome_dependency_conflicts(
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_leak",
                        "claim": "The expected values require a different runtime action.",
                        "falsified_if": "The target result is consistent with the current action.",
                    },
                    {
                        "hypothesis_id": "h_runtime",
                        "claim": "The final artifact contains an error value after conversion.",
                        "falsified_if": "Reading the final artifact returns valid values.",
                    },
                ]
            }
        )

        assert conflicts == [
            "hypothesis h_leak uses evaluator-owned outcomes in claim",
            "hypothesis h_leak uses evaluator-owned outcomes in falsified_if",
        ]
        correction = _build_causal_plan_correction_prompt(
            "{}",
            '{"causal_investigation":{}}',
            validation_conflicts=conflicts,
        )
        assert conflicts[0] in correction
        assert "independently observable prediction" in correction

    def test_causal_plan_salvages_outcome_independent_siblings(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _normalize_outcome_independent_causal_plan,
        )

        requirement_id = "criterion:opaque"
        raw = {
            "causal_investigation": {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_trace",
                        "claim": "The Agent released a conclusion before checking the cited source span.",
                        "explains_requirement_ids": [requirement_id],
                        "current_support": ["trial_1:message_8"],
                        "falsified_if": "The trace contains a source read covering every cited ground before release.",
                        "evidence_requests": [
                            {
                                "request_id": "q_trace",
                                "operation": "search_trace",
                                "query": "source read cited ground release",
                            }
                        ],
                    },
                    {
                        "hypothesis_id": "h_scope",
                        "claim": "A material decision ground was used without a task-visible scope check.",
                        "explains_requirement_ids": [requirement_id],
                        "current_support": ["trial_1:message_12"],
                        "falsified_if": "Every material ground has a scope witness in the trace or artifact.",
                        "evidence_requests": [
                            {
                                "request_id": "q_scope",
                                "operation": "search_trace",
                                "query": "scope witness material ground",
                            }
                        ],
                    },
                    {
                        "hypothesis_id": "h_leak",
                        "claim": "The expected answer requires a different conclusion.",
                        "explains_requirement_ids": [requirement_id],
                        "current_support": ["grader"],
                        "falsified_if": "The target result matches the released conclusion.",
                        "evidence_requests": [
                            {
                                "request_id": "q_leak",
                                "operation": "inspect_evaluation",
                                "query": "target result",
                            }
                        ],
                    },
                ]
            }
        }

        plan, conflicts = _normalize_outcome_independent_causal_plan(
            raw,
            failed_requirement_ids=[requirement_id],
        )

        assert conflicts
        assert plan is not None
        assert [item["hypothesis_id"] for item in plan["hypotheses"]] == ["h_trace", "h_scope"]
        assert {item["request_id"] for item in plan["evidence_requests"]} == {"q_trace", "q_scope"}

    def test_structurally_invalid_plan_still_reports_outcome_leakage(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _normalize_outcome_independent_causal_plan,
        )

        plan, conflicts = _normalize_outcome_independent_causal_plan(
            {
                "causal_investigation": {
                    "hypotheses": [
                        {
                            "hypothesis_id": "h_leak",
                            "claim": "The expected answer requires a different action.",
                            "explains_requirement_ids": ["criterion:opaque"],
                            "falsified_if": "The target result agrees with the current action.",
                            "evidence_requests": [],
                        }
                    ]
                }
            },
            failed_requirement_ids=["criterion:opaque"],
        )

        assert plan is None
        assert "hypothesis h_leak uses evaluator-owned outcomes in claim" in conflicts

    def test_outcome_independent_plan_rejects_hidden_evaluation_requests(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _normalize_outcome_independent_causal_plan,
        )

        hypotheses = []
        for index in (1, 2):
            hypotheses.append(
                {
                    "hypothesis_id": f"h{index}",
                    "claim": f"Runtime decision mechanism {index} released an unsupported claim.",
                    "explains_requirement_ids": ["criterion:opaque"],
                    "current_support": ["the judge target value disagrees"],
                    "falsified_if": f"Task-visible evidence supports mechanism {index} before release.",
                    "evidence_requests": [
                        {
                            "request_id": f"q{index}",
                            "operation": "inspect_evaluation",
                            "query": "judge target value",
                        }
                    ],
                }
            )

        plan, conflicts = _normalize_outcome_independent_causal_plan(
            {"causal_investigation": {"hypotheses": hypotheses}},
            failed_requirement_ids=["criterion:opaque"],
        )

        assert plan is None
        assert any("current_support" in item for item in conflicts)
        assert any("inspect_evaluation" in item for item in conflicts)

    def test_outcome_independent_recovery_input_keeps_behavior_but_removes_labels(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_outcome_independent_causal_plan_recovery_prompt,
        )

        diagnosis_input = json.dumps(
            {
                "authoritative_task_contract": {"input_excerpt": "Inspect the artifact and answer."},
                "authoritative_benchmark_test_contract": {"expected_answer": "YES"},
                "primary_evidence": {
                    "evidence_summary_text": "grader expects YES",
                    "causal_digest": {
                        "outcome": {"score": 0.0, "judge_evidence": "YES"},
                        "trials": [
                            {
                                "trace_id": "trial_1",
                                "final_output": {"excerpt": "The Agent answered NO after reading clause 4."},
                                "trial_evaluation": {"score": 0.0, "passed": False},
                            }
                        ],
                    },
                },
                "deterministic_failed_requirement_inventory": {
                    "items": [{"requirement_id": "criterion:opaque", "expected": "YES"}]
                },
                "case_facts": {
                    "case_id": "case_1",
                    "score": 0.0,
                    "evaluation_reason": "expected YES",
                },
            }
        )

        prompt = _build_outcome_independent_causal_plan_recovery_prompt(
            diagnosis_input,
            failed_requirement_ids=["criterion:opaque"],
            validation_conflicts=["hypothesis h1 uses evaluator-owned outcomes in claim"],
        )

        task_visible = prompt.split("TASK_VISIBLE_DIAGNOSIS_INPUT:\n", maxsplit=1)[1]
        assert "The Agent answered NO after reading clause 4." in task_visible
        assert '"expected_answer":"YES"' not in task_visible
        assert '"score":0.0' not in task_visible
        assert "grader expects YES" not in task_visible
        assert '"requirement_id":"criterion:opaque"' in task_visible

    def test_supported_sibling_hypothesis_cannot_disappear_from_handoff(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _causal_investigation_conflicts,
        )

        diagnosis = self._diagnosis(
            failure_mode="two_supported_mechanisms",
            failed_check="criterion:value",
            observable="two independent runtime mechanisms were observed",
        )
        diagnosis["selected_hypothesis_id"] = "h1"
        diagnosis["hypothesis_assessment"] = [
            {
                "hypothesis_id": hypothesis_id,
                "status": "supported",
                "falsifying_condition_status": "not_observed",
                "claim_follows_from_evidence": "yes",
                "logic_check": f"{request_id} supports the runtime mechanism",
                "controller_request_ids": [request_id],
            }
            for hypothesis_id, request_id in (("h1", "q1"), ("h2", "q2"))
        ]
        investigation = {
            "hypotheses": [
                {"hypothesis_id": "h1", "explains_requirement_ids": ["criterion:value"]},
                {"hypothesis_id": "h2", "explains_requirement_ids": ["criterion:value"]},
            ],
            "evidence_requests": [
                {"request_id": "q1", "hypothesis_ids": ["h1"], "operation": "read_event"},
                {"request_id": "q2", "hypothesis_ids": ["h2"], "operation": "read_event"},
            ],
        }
        evidence = {
            "results": [
                {"request_id": "q1", "availability": "available"},
                {"request_id": "q2", "availability": "available"},
            ]
        }

        conflicts = _causal_investigation_conflicts(
            [diagnosis],
            investigation,
            evidence_results=evidence,
            prior_candidate_feedback=None,
        )
        assert "supported causal hypothesis was not handed off or explicitly disposed: h2" in conflicts

        diagnosis["hypothesis_assessment"][1].update(
            {
                "handoff_disposition": "non_actionable",
                "handoff_reason": "the observed environment behavior has no Harness-controlled decision",
            }
        )
        conflicts = _causal_investigation_conflicts(
            [diagnosis],
            investigation,
            evidence_results=evidence,
            prior_candidate_feedback=None,
        )
        assert not any("supported causal hypothesis was not handed off" in item for item in conflicts)

    def test_normalize_keeps_independent_surfaces_for_the_same_failed_check(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _normalize_case_diagnoses

        extraction = self._diagnosis(
            failure_mode="incomplete_extraction",
            failed_check="criterion:value",
            observable="the final value is wrong",
            target_ref="member_harness.solver.skill",
        )
        extraction["critical_mistake"] = "the source read stopped before the controlling clause"
        calculation = self._diagnosis(
            failure_mode="wrong_calculation",
            failed_check="criterion:value",
            observable="the final value is wrong",
            target_ref="member_harness.solver.tool",
        )
        calculation["critical_mistake"] = "the calculator used the wrong operands"

        normalized = _normalize_case_diagnoses({"diagnoses": [extraction, calculation]})

        assert len(normalized) == 2
        assert {item["target_ref"] for item in normalized} == {
            "member_harness.solver.skill",
            "member_harness.solver.tool",
        }

    def test_causal_refinement_runs_when_a_failed_requirement_is_only_residual(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _diagnoses_need_causal_refinement

        diagnoses = [
            {
                "evidence_status": "supported_hypothesis",
                "causal_coverage": {
                    "explained_requirement_ids": ["criterion:value"],
                    "residual_requirement_ids": ["criterion:format"],
                    "sufficiency_status": "local_contributor",
                },
                "hypothesis_assessment": [{"hypothesis_id": "h1", "status": "supported"}],
            }
        ]

        assert _diagnoses_need_causal_refinement(
            diagnoses,
            failed_requirement_ids=["criterion:value", "criterion:format"],
        )

    @pytest.mark.asyncio
    async def test_per_case_diagnosis_flattens_wrapped_diagnoses(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as analyzer_module
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        case_dir = tmp_path / "case_results" / "case_multi"
        result_path = case_dir / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        case = CaseAnalysisInput(
            case_id="case_multi",
            status="failed",
            score=0.0,
            input="produce both required outputs",
            expected=None,
            response="partial output",
            error="",
            evaluation_method="script_based",
            evaluation_passed=False,
            evaluation_reason="two independent checks failed",
            evaluation_metadata={},
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )
        strategy = analyzer_module.DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
        )
        diagnoses = [
            self._diagnosis(
                failure_mode="missing_payment",
                failed_check="payment_b_check",
                observable="Payment B appears in the output",
            ),
            self._diagnosis(
                failure_mode="wrong_formula",
                failed_check="formula_check",
                observable="the computed formula matches the contract",
                target_ref="member_harness.solver.prompt",
            ),
        ]

        async def fake_build_agent(workspace: str) -> dict[str, str]:
            return {"workspace": workspace}

        async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
            assert "CAUSAL_INVESTIGATION_PHASE=plan" in prompt
            return json.dumps({"diagnoses": diagnoses})

        monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
        monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
        monkeypatch.setattr(
            analyzer_module,
            "_diagnosis_validation_conflicts",
            lambda *args, **kwargs: [],
        )

        results = await strategy._per_case_diagnosis(
            [case],
            DeterministicSignals(method="script_based"),
            None,
        )

        assert [item["failure_mode"] for item in results] == [
            "missing_payment",
            "wrong_formula",
        ]
        assert [item["case_id"] for item in results] == ["case_multi", "case_multi"]
        assert [item["diagnosis_index"] for item in results] == [1, 2]
        assert all(item["diagnosis_count"] == 2 for item in results)

    def test_aggregation_keeps_distinct_clusters_from_same_case_independent(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _aggregate_structured_diagnoses,
        )

        first = self._diagnosis(
            failure_mode="missing_required_output",
            failed_check="payment_b_check",
            observable="Payment B appears in the output",
        )
        second = self._diagnosis(
            failure_mode="missing_required_output",
            failed_check="formula_check",
            observable="the computed formula matches the contract",
        )
        for diagnosis in (first, second):
            diagnosis.update(
                {
                    "evidence_status": "supported_hypothesis",
                    "failed_requirement": "one explicit output requirement was not satisfied",
                    "competing_hypotheses": ["instruction gap", "tool result handling gap"],
                    "discriminating_evidence": "the trace shows the omitted final-output action",
                }
            )
        per_case = [
            {"case_id": "case_multi", **first},
            {"case_id": "case_multi", **second},
        ]

        issues = _aggregate_structured_diagnoses(
            per_case_results=per_case,
            max_issues=5,
            evidence_limit_per_issue=3,
        )

        assert len(issues) == 2
        assert {tuple(issue.metadata["attribution"]["failure_cluster"]["failed_checks"]) for issue in issues} == {
            ("payment_b_check",),
            ("formula_check",),
        }
        assert all(issue.affected_cases == ["case_multi"] for issue in issues)
        assert all(issue.metadata["attribution"]["evidence_status"] == "supported_hypothesis" for issue in issues)
        assert all(issue.metadata["attribution"]["competing_hypotheses"] for issue in issues)


class TestDeterministicAggregation:
    @pytest.mark.asyncio
    async def test_aggregate_diagnosis_uses_structured_per_case_targets_without_agent(
        self,
        tmp_path: Path,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            DiagnosisAgentStrategy,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            DeterministicSignals,
            EvaluationSummaryInput,
        )

        strategy = DiagnosisAgentStrategy(
            EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml", causal_investigation_required=False)
        )

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
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation

        analyzer = EvaluationResultAnalyzer(
            EvaluationResultAnalyzerConfig(output_filename="issues.yaml"),
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

        assert analysis_ref["metadata"]["analysis_status"] == "empty_case_results"
        assert analysis_ref["issues"] == []
        assert issues_payload == {"issues": []}
        assert issues_path.name == "issues.yaml"

    @pytest.mark.asyncio
    async def test_analysis_ref_backfills_case_evidence_refs(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation

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

        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation

        artifacts = _write_evaluation_artifacts(tmp_path, method="llm_as_judge")
        output_dir = tmp_path / "analysis"
        analyzer = EvaluationResultAnalyzer(
            EvaluationResultAnalyzerConfig(
                model_config_ref=str(model_config_path),
                max_issues=3,
                evidence_limit_per_issue=3,
                output_filename="team_issues.yaml",
                causal_investigation_required=False,
            ),
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

        from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            EvaluationResultAnalyzer,
        )
        from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation

        analyzer = EvaluationResultAnalyzer(
            EvaluationResultAnalyzerConfig(
                model_config_ref=str(model_config_path),
                output_filename="team_issues.yaml",
                causal_investigation_required=False,
            ),
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
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import EvaluationSummaryInput

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
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput

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
