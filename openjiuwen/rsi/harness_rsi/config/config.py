# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configuration models for standalone Harness self-improvement."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from openjiuwen.rsi.harness_rsi.model_call import DEFAULT_MODEL_CALL_MAX_RETRIES


@dataclass(frozen=True, slots=True)
class DataLoaderConfig:
    """Loading and batching for an existing evaluation dataset."""

    file_pattern: str = "*.json"
    batch_size: int = 1
    batch_balance_keys: list[str] = field(default_factory=lambda: ["dimension", "difficulty", "source", "task_type"])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataLoaderConfig":
        return cls(
            file_pattern=str(data.get("file_pattern", "*.json")),
            batch_size=_int_value(data.get("batch_size"), default=1),
            batch_balance_keys=_string_list(
                data.get("batch_balance_keys"),
                default=["dimension", "difficulty", "source", "task_type"],
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelConfigs:
    """Model references used by the three runtime stages."""

    evaluation: str = ""
    analysis: str = ""
    member_optimization: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelConfigs":
        return cls(
            evaluation=str(data.get("evaluation", "")),
            analysis=str(data.get("analysis", "")),
            member_optimization=str(data.get("member_optimization", "")),
        )


@dataclass(frozen=True, slots=True)
class EvaluatorConfig:
    """Standalone Harness execution and deterministic scoring."""

    model_config_ref: str = ""
    backend: str = "single_harness"
    evaluation_method: str = "script-based"
    transient_case_retry_limit: int = 2
    solver_backend: str = "deep_agent"
    jiuwenswarm_executable: str = ""
    jiuwenswarm_python: str = ""
    jiuwenswarm_expected_version: str = ""
    jiuwenswarm_startup_timeout_sec: int = 120
    jiuwenswarm_runtime_timeout_sec: int = 3600
    jiuwenswarm_runtime_profile: str = "task86"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluatorConfig":
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            backend=str(data.get("backend", "single_harness")),
            evaluation_method=str(data.get("evaluation_method", "script-based")),
            transient_case_retry_limit=_int_value(
                data.get("transient_case_retry_limit"),
                default=2,
            ),
            solver_backend=str(data.get("solver_backend", "deep_agent")),
            jiuwenswarm_executable=str(data.get("jiuwenswarm_executable", "")),
            jiuwenswarm_python=str(data.get("jiuwenswarm_python", "")),
            jiuwenswarm_expected_version=str(data.get("jiuwenswarm_expected_version", "")),
            jiuwenswarm_startup_timeout_sec=_int_value(
                data.get("jiuwenswarm_startup_timeout_sec"),
                default=120,
            ),
            jiuwenswarm_runtime_timeout_sec=_int_value(
                data.get("jiuwenswarm_runtime_timeout_sec"),
                default=3600,
            ),
            jiuwenswarm_runtime_profile=str(data.get("jiuwenswarm_runtime_profile", "task86")),
        )


@dataclass(frozen=True, slots=True)
class EvaluationResultAnalyzerConfig:
    """Post-evaluation causal diagnosis."""

    model_config_ref: str = ""
    diagnosis_agent_model_config_ref: str = ""
    diagnosis_agent_max_retries: int = DEFAULT_MODEL_CALL_MAX_RETRIES
    diagnosis_agent_max_concurrency: int = 5
    diagnosis_agent_max_iterations: int = 20
    diagnosis_agent_max_tokens: int = 16384
    causal_investigation_required: bool = True
    max_issues: int = 20
    evidence_limit_per_issue: int = 5
    output_filename: str = "issues.yaml"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluationResultAnalyzerConfig":
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            diagnosis_agent_model_config_ref=str(data.get("diagnosis_agent_model_config_ref", "")),
            diagnosis_agent_max_retries=_int_value(
                data.get("diagnosis_agent_max_retries"),
                default=DEFAULT_MODEL_CALL_MAX_RETRIES,
            ),
            diagnosis_agent_max_concurrency=_int_value(
                data.get("diagnosis_agent_max_concurrency"),
                default=5,
            ),
            diagnosis_agent_max_iterations=_int_value(
                data.get("diagnosis_agent_max_iterations"),
                default=20,
            ),
            diagnosis_agent_max_tokens=_int_value(
                data.get("diagnosis_agent_max_tokens"),
                default=16384,
            ),
            causal_investigation_required=_bool_value(
                data.get("causal_investigation_required"),
                default=True,
            ),
            max_issues=_int_value(data.get("max_issues"), default=20),
            evidence_limit_per_issue=_int_value(data.get("evidence_limit_per_issue"), default=5),
            output_filename=str(data.get("output_filename", "issues.yaml")),
        )


@dataclass(frozen=True, slots=True)
class MemberOptimizerConfig:
    """Candidate generation and verification for one Expert Harness."""

    model_config_ref: str = ""
    action_group_configs: list[str] = field(default_factory=list)
    agent_skills_dirs: list[str] = field(default_factory=list)
    freeze: bool = False
    max_roles_per_run: int = 2
    min_attribution_confidence: float = 0.5
    attribution_retry_limit: int = DEFAULT_MODEL_CALL_MAX_RETRIES
    stage_retry_limit: int = DEFAULT_MODEL_CALL_MAX_RETRIES
    max_cases_per_issue: int = 3
    max_trace_excerpt_chars: int = 4000
    max_result_excerpt_chars: int = 2000
    allow_empty_harness_refs_noop: bool = True
    execution_concurrency: int = 2
    role_execution_concurrency: int = 2
    action_execution_concurrency_per_role: int = 2
    allowed_action_groups: list[str] = field(default_factory=list)
    allowed_prompt_surfaces: list[str] = field(default_factory=list)
    max_actions_per_plan: int = 0
    max_issue_attempts_per_batch: int = 0
    max_repair_rounds_per_batch: int = 3
    sibling_candidate_count: int = 1
    improver_policy_ref: str = ""
    candidate_min_score_delta: float = 0.0
    candidate_min_target_behavior_delta: float = 0.0
    candidate_non_target_max_regression: float = 0.0
    candidate_holdout_cases: int = 0
    candidate_holdout_max_regression: float = 0.0
    adapt_frozen_team_issues: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemberOptimizerConfig":
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            action_group_configs=_string_list(data.get("action_group_configs")),
            agent_skills_dirs=_string_list(data.get("agent_skills_dirs")),
            freeze=_bool_value(data.get("freeze"), default=False),
            max_roles_per_run=_int_value(data.get("max_roles_per_run"), default=2),
            min_attribution_confidence=_float_value(data.get("min_attribution_confidence"), default=0.5),
            attribution_retry_limit=_int_value(
                data.get("attribution_retry_limit"),
                default=DEFAULT_MODEL_CALL_MAX_RETRIES,
            ),
            stage_retry_limit=_int_value(
                data.get("stage_retry_limit"),
                default=DEFAULT_MODEL_CALL_MAX_RETRIES,
            ),
            max_cases_per_issue=_int_value(data.get("max_cases_per_issue"), default=3),
            max_trace_excerpt_chars=_int_value(data.get("max_trace_excerpt_chars"), default=4000),
            max_result_excerpt_chars=_int_value(data.get("max_result_excerpt_chars"), default=2000),
            allow_empty_harness_refs_noop=_bool_value(
                data.get("allow_empty_harness_refs_noop"),
                default=True,
            ),
            execution_concurrency=_int_value(data.get("execution_concurrency"), default=2),
            role_execution_concurrency=_int_value(data.get("role_execution_concurrency"), default=2),
            action_execution_concurrency_per_role=_int_value(
                data.get("action_execution_concurrency_per_role"),
                default=2,
            ),
            allowed_action_groups=_string_list(data.get("allowed_action_groups")),
            allowed_prompt_surfaces=_string_list(data.get("allowed_prompt_surfaces")),
            max_actions_per_plan=_int_value(data.get("max_actions_per_plan"), default=0),
            max_issue_attempts_per_batch=_int_value(
                data.get("max_issue_attempts_per_batch"),
                default=0,
            ),
            max_repair_rounds_per_batch=_int_value(
                data.get("max_repair_rounds_per_batch"),
                default=3,
            ),
            sibling_candidate_count=_int_value(
                data.get("sibling_candidate_count"),
                default=1,
            ),
            improver_policy_ref=str(data.get("improver_policy_ref", "")),
            candidate_min_score_delta=_float_value(data.get("candidate_min_score_delta"), default=0.0),
            candidate_min_target_behavior_delta=_float_value(
                data.get("candidate_min_target_behavior_delta"),
                default=0.0,
            ),
            candidate_non_target_max_regression=_float_value(
                data.get("candidate_non_target_max_regression"),
                default=0.0,
            ),
            candidate_holdout_cases=_int_value(data.get("candidate_holdout_cases"), default=0),
            candidate_holdout_max_regression=_float_value(
                data.get("candidate_holdout_max_regression"),
                default=0.0,
            ),
            adapt_frozen_team_issues=_bool_value(data.get("adapt_frozen_team_issues"), default=False),
        )


@dataclass(frozen=True, slots=True)
class OrchestratorSchedulingConfig:
    """Selection policy for the iterative single-Harness loop."""

    evaluation_strategy: str = "hybrid"
    coordination_strategy: str = "team_first_single_pass"
    promotion_policy: str = "epoch_full_evaluation"
    full_evaluation_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrchestratorSchedulingConfig":
        config = cls(
            evaluation_strategy=str(data.get("evaluation_strategy", "hybrid")),
            coordination_strategy=str(data.get("coordination_strategy", "team_first_single_pass")),
            promotion_policy=str(data.get("promotion_policy", "epoch_full_evaluation")),
            full_evaluation_enabled=_bool_value(data.get("full_evaluation_enabled"), default=True),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.evaluation_strategy != "hybrid":
            raise ValueError("scheduling.evaluation_strategy must be hybrid")
        if self.coordination_strategy != "team_first_single_pass":
            raise ValueError("scheduling.coordination_strategy must be team_first_single_pass")
        if self.promotion_policy != "epoch_full_evaluation":
            raise ValueError("scheduling.promotion_policy must be epoch_full_evaluation")


@dataclass(frozen=True, slots=True)
class AutoCoordinatingHarnessConfig:
    """Top-level standalone Harness optimization configuration."""

    workspace_dir: str = ""
    max_epochs: int = 1
    model_configs: ModelConfigs = field(default_factory=ModelConfigs)
    data_loader: DataLoaderConfig = field(default_factory=DataLoaderConfig)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    evaluation_result_analyzer: EvaluationResultAnalyzerConfig = field(default_factory=EvaluationResultAnalyzerConfig)
    member_optimizer: MemberOptimizerConfig = field(default_factory=MemberOptimizerConfig)
    scheduling: OrchestratorSchedulingConfig = field(default_factory=OrchestratorSchedulingConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoCoordinatingHarnessConfig":
        model_configs = ModelConfigs.from_dict(_mapping(data.get("model_configs")))
        evaluator = EvaluatorConfig.from_dict(_mapping(data.get("evaluator")))
        analyzer = EvaluationResultAnalyzerConfig.from_dict(_mapping(data.get("evaluation_result_analyzer")))
        optimizer = MemberOptimizerConfig.from_dict(_mapping(data.get("member_optimizer")))
        model_configs = ModelConfigs(
            evaluation=model_configs.evaluation or evaluator.model_config_ref,
            analysis=model_configs.analysis or analyzer.model_config_ref,
            member_optimization=model_configs.member_optimization or optimizer.model_config_ref,
        )
        config = cls(
            workspace_dir=str(data.get("workspace_dir", "")),
            max_epochs=_int_value(data.get("max_epochs"), default=1),
            model_configs=model_configs,
            data_loader=DataLoaderConfig.from_dict(_mapping(data.get("data_loader"))),
            evaluator=replace(evaluator, model_config_ref=model_configs.evaluation),
            evaluation_result_analyzer=replace(analyzer, model_config_ref=model_configs.analysis),
            member_optimizer=replace(optimizer, model_config_ref=model_configs.member_optimization),
            scheduling=OrchestratorSchedulingConfig.from_dict(_mapping(data.get("scheduling"))),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.max_epochs < 1:
            raise ValueError("max_epochs must be greater than or equal to 1")
        if self.data_loader.batch_size < 1:
            raise ValueError("data_loader.batch_size must be greater than or equal to 1")
        if not self.data_loader.batch_balance_keys:
            raise ValueError("data_loader.batch_balance_keys must not be empty")
        if self.evaluation_result_analyzer.max_issues < 1:
            raise ValueError("evaluation_result_analyzer.max_issues must be greater than or equal to 1")
        if self.evaluation_result_analyzer.evidence_limit_per_issue < 1:
            raise ValueError("evaluation_result_analyzer.evidence_limit_per_issue must be greater than or equal to 1")
        if self.evaluator.solver_backend not in {"deep_agent", "jiuwenswarm"}:
            raise ValueError("evaluator.solver_backend must be one of: deep_agent, jiuwenswarm")
        if self.evaluator.jiuwenswarm_startup_timeout_sec < 1:
            raise ValueError("evaluator.jiuwenswarm_startup_timeout_sec must be greater than or equal to 1")
        if self.evaluator.jiuwenswarm_runtime_timeout_sec < 1:
            raise ValueError("evaluator.jiuwenswarm_runtime_timeout_sec must be greater than or equal to 1")
        if self.member_optimizer.max_issue_attempts_per_batch < 0:
            raise ValueError("member_optimizer.max_issue_attempts_per_batch must be greater than or equal to 0")
        if self.member_optimizer.max_repair_rounds_per_batch < 1:
            raise ValueError("member_optimizer.max_repair_rounds_per_batch must be greater than or equal to 1")
        if self.member_optimizer.sibling_candidate_count < 1:
            raise ValueError("member_optimizer.sibling_candidate_count must be greater than or equal to 1")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any, *, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value if item is not None]
    raise ValueError(f"expected a list of strings, got {type(value).__name__}")


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "on"}:
            return True
        if normalized in {"false", "no", "n", "0", "off"}:
            return False
    raise ValueError(f"expected a boolean value, got {value!r}")


def _int_value(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"expected an integer value, got {value!r}")
    return int(value)


def _float_value(value: Any, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"expected a numeric value, got {value!r}")
    return float(value)


__all__ = [
    "AutoCoordinatingHarnessConfig",
    "DataLoaderConfig",
    "EvaluationResultAnalyzerConfig",
    "EvaluatorConfig",
    "MemberOptimizerConfig",
    "ModelConfigs",
    "OrchestratorSchedulingConfig",
]
