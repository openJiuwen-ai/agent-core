# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataclass configuration models for the auto-coordinating harness."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from openjiuwen.rsi.model_call import (
    DEFAULT_MODEL_CALL_MAX_RETRIES,
)


@dataclass(frozen=True, slots=True)
class DataLoaderConfig:
    """Configuration boundary for loading existing evaluation datasets."""

    file_pattern: str = "*.json"
    batch_size: int = 1
    batch_balance_keys: list[str] = field(default_factory=lambda: ["dimension", "difficulty", "source", "task_type"])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataLoaderConfig":
        """Build config from a YAML mapping."""
        return cls(
            file_pattern=str(data.get("file_pattern", "*.json")),
            batch_size=_int_value(data.get("batch_size"), default=1),
            batch_balance_keys=_string_list(
                data.get("batch_balance_keys"),
                default=["dimension", "difficulty", "source", "task_type"],
            ),
        )


@dataclass(frozen=True, slots=True)
class DatasetCurationConfig:
    """Configuration for mining replay datasets from evaluated traces."""

    enabled: bool = True
    score_threshold: float = 1.0
    require_judgeable_reference: bool = True
    output_filename: str = "replay_cases.json"
    report_filename: str = "curation_report.yaml"
    targeted_seed_filename: str = "targeted_dataset_seed.json"
    source_label: str = "trace_replay"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetCurationConfig":
        """Build config from a YAML mapping."""
        return cls(
            enabled=_bool_value(data.get("enabled"), default=True),
            score_threshold=_float_value(data.get("score_threshold"), default=1.0),
            require_judgeable_reference=_bool_value(
                data.get("require_judgeable_reference"),
                default=True,
            ),
            output_filename=str(data.get("output_filename", "replay_cases.json")),
            report_filename=str(data.get("report_filename", "curation_report.yaml")),
            targeted_seed_filename=str(data.get("targeted_seed_filename", "targeted_dataset_seed.json")),
            source_label=str(data.get("source_label", "trace_replay")),
        )


@dataclass(frozen=True, slots=True)
class ModelConfigs:
    """Top-level model configuration references for one optimization run."""

    evaluation: str = ""
    judge: str = ""
    analysis: str = ""
    team_skill_optimization: str = ""
    member_optimization: str = ""
    experience_learning: str = ""
    dataset_generation: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelConfigs":
        """Build model config refs from a YAML mapping."""
        return cls(
            evaluation=str(data.get("evaluation", "")),
            judge=str(data.get("judge", "")),
            analysis=str(data.get("analysis", "")),
            team_skill_optimization=str(data.get("team_skill_optimization", "")),
            member_optimization=str(data.get("member_optimization", "")),
            experience_learning=str(data.get("experience_learning", "")),
            dataset_generation=str(data.get("dataset_generation", "")),
        )


@dataclass(frozen=True, slots=True)
class DatasetGeneratorConfig:
    """Configuration boundary for dataset generation."""

    model_config_ref: str = ""
    min_cases: int = 0
    coverage_dimensions: list[str] = field(default_factory=list)
    known_failures_ref: str = ""
    quality_review_enabled: bool = True
    quality_score_threshold: int = 8
    capability_alignment_score_threshold: int = 8
    verifiability_score_threshold: int = 8
    difficulty_score_threshold: int = 3

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetGeneratorConfig":
        """Build config from a YAML mapping."""
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            min_cases=_int_value(data.get("min_cases"), default=0),
            coverage_dimensions=_string_list(data.get("coverage_dimensions")),
            known_failures_ref=str(data.get("known_failures_ref", "")),
            quality_review_enabled=_bool_value(
                data.get("quality_review_enabled"),
                default=True,
            ),
            quality_score_threshold=_int_value(
                data.get("quality_score_threshold"),
                default=8,
            ),
            capability_alignment_score_threshold=_int_value(
                data.get("capability_alignment_score_threshold"),
                default=8,
            ),
            verifiability_score_threshold=_int_value(
                data.get("verifiability_score_threshold"),
                default=8,
            ),
            difficulty_score_threshold=_int_value(
                data.get("difficulty_score_threshold"),
                default=3,
            ),
        )


@dataclass(frozen=True, slots=True)
class SeedEvaluationConfig:
    """Configuration for the initial real-task delivery check."""

    enabled: bool = False
    pass_threshold: float = 0.8
    excellent_threshold: float = 0.99
    max_cases: int = 20

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SeedEvaluationConfig":
        """Build seed evaluation config from a YAML mapping."""
        return cls(
            enabled=_bool_value(data.get("enabled"), default=False),
            pass_threshold=_float_value(data.get("pass_threshold"), default=0.8),
            excellent_threshold=_float_value(
                data.get("excellent_threshold"),
                default=0.99,
            ),
            max_cases=_int_value(data.get("max_cases"), default=20),
        )


@dataclass(frozen=True, slots=True)
class EvaluatorConfig:
    """Configuration boundary for Team evaluation."""

    model_config_ref: str = ""
    judge_model_config_ref: str = ""
    team_spec_config_ref: str = ""
    default_script: str = "default"
    model_name: str = ""
    model_url: str = ""
    model_api_key: str = ""
    model_provider: str = "OpenAI"
    backend: str = "local"
    evaluation_method: str = "llm-as-judge"
    script_configs: list[str] = field(default_factory=list)
    success_score: float = 1.0
    case_lifecycle_timeout_sec: float = 3600.0
    transient_case_retry_limit: int = 2

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluatorConfig":
        """Build config from a YAML mapping."""
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            judge_model_config_ref=str(data.get("judge_model_config_ref", "")),
            team_spec_config_ref=str(data.get("team_spec_config_ref", "")),
            default_script=str(data.get("default_script", "default")),
            model_name=str(data.get("model_name", "")),
            model_url=str(data.get("model_url", "")),
            model_api_key=str(data.get("model_api_key", "")),
            model_provider=str(data.get("model_provider", "OpenAI")),
            backend=str(data.get("backend", "local")),
            evaluation_method=str(data.get("evaluation_method", "llm-as-judge")),
            script_configs=_string_list(data.get("script_configs")),
            success_score=_float_value(data.get("success_score"), default=1.0),
            case_lifecycle_timeout_sec=_float_value(
                data.get("case_lifecycle_timeout_sec"),
                default=3600.0,
            ),
            transient_case_retry_limit=_int_value(
                data.get("transient_case_retry_limit"),
                default=2,
            ),
        )


@dataclass(frozen=True, slots=True)
class EvaluationResultAnalyzerConfig:
    """Configuration boundary for post-evaluation Team issue analysis."""

    model_config_ref: str = ""
    diagnosis_agent_model_config_ref: str = ""
    diagnosis_agent_max_retries: int = DEFAULT_MODEL_CALL_MAX_RETRIES
    diagnosis_agent_max_concurrency: int = 5
    diagnosis_agent_max_iterations: int = 20
    max_issues: int = 20
    evidence_limit_per_issue: int = 5
    output_filename: str = "issues.yaml"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvaluationResultAnalyzerConfig":
        """Build config from a YAML mapping."""
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            diagnosis_agent_model_config_ref=str(data.get("diagnosis_agent_model_config_ref", "")),
            diagnosis_agent_max_retries=_int_value(
                data.get("diagnosis_agent_max_retries"),
                default=DEFAULT_MODEL_CALL_MAX_RETRIES,
            ),
            diagnosis_agent_max_concurrency=_int_value(data.get("diagnosis_agent_max_concurrency"), default=5),
            diagnosis_agent_max_iterations=_int_value(data.get("diagnosis_agent_max_iterations"), default=20),
            max_issues=_int_value(data.get("max_issues"), default=20),
            evidence_limit_per_issue=_int_value(data.get("evidence_limit_per_issue"), default=5),
            output_filename=str(data.get("output_filename", "issues.yaml")),
        )


@dataclass(frozen=True, slots=True)
class TeamSkillOptimizerConfig:
    """Configuration boundary for Team Skill optimization."""

    model_config_ref: str = ""
    max_candidates: int = 1
    freeze: bool = False
    language: str = "cn"
    auto_approve: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamSkillOptimizerConfig":
        """Build config from a YAML mapping."""
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            max_candidates=_int_value(data.get("max_candidates"), default=1),
            freeze=_bool_value(data.get("freeze"), default=False),
            language=str(data.get("language", "cn")),
            auto_approve=_bool_value(data.get("auto_approve"), default=True),
        )


@dataclass(frozen=True, slots=True)
class MemberOptimizerConfig:
    """Configuration boundary for member harness optimization.

    Extended per feat_009 Section 2.6.4 design decisions.
    """

    model_config_ref: str = ""
    action_group_configs: list[str] = field(default_factory=list)
    agent_skills_dirs: list[str] = field(default_factory=list)
    freeze: bool = False
    # feat_009 design decisions:
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
    candidate_min_score_delta: float = 0.0
    candidate_min_target_behavior_delta: float = 0.0
    candidate_non_target_max_regression: float = 0.0
    candidate_holdout_cases: int = 0
    candidate_holdout_max_regression: float = 0.0
    adapt_frozen_team_issues: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemberOptimizerConfig":
        """Build config from a YAML mapping."""
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
            allow_empty_harness_refs_noop=_bool_value(data.get("allow_empty_harness_refs_noop"), default=True),
            execution_concurrency=_int_value(data.get("execution_concurrency"), default=2),
            role_execution_concurrency=_int_value(data.get("role_execution_concurrency"), default=2),
            action_execution_concurrency_per_role=_int_value(
                data.get("action_execution_concurrency_per_role"), default=2
            ),
            allowed_action_groups=_string_list(data.get("allowed_action_groups")),
            allowed_prompt_surfaces=_string_list(data.get("allowed_prompt_surfaces")),
            max_actions_per_plan=_int_value(data.get("max_actions_per_plan"), default=0),
            candidate_min_score_delta=_float_value(data.get("candidate_min_score_delta"), default=0.0),
            candidate_min_target_behavior_delta=_float_value(
                data.get("candidate_min_target_behavior_delta"), default=0.0
            ),
            candidate_non_target_max_regression=_float_value(
                data.get("candidate_non_target_max_regression"), default=0.0
            ),
            candidate_holdout_cases=_int_value(data.get("candidate_holdout_cases"), default=0),
            candidate_holdout_max_regression=_float_value(data.get("candidate_holdout_max_regression"), default=0.0),
            adapt_frozen_team_issues=_bool_value(data.get("adapt_frozen_team_issues"), default=False),
        )


@dataclass(frozen=True, slots=True)
class OptimizationExperienceLearnerConfig:
    """Configuration boundary for cross-case optimization experience learning."""

    model_config_ref: str = ""
    output_filename: str = "experience_ref.yaml"
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OptimizationExperienceLearnerConfig":
        """Build config from a YAML mapping."""
        return cls(
            model_config_ref=str(data.get("model_config_ref", "")),
            output_filename=str(data.get("output_filename", "experience_ref.yaml")),
            enabled=_bool_value(data.get("enabled"), default=True),
        )


@dataclass(frozen=True, slots=True)
class OrchestratorSchedulingConfig:
    """Fixed scheduling strategy used by the orchestrator closed loop."""

    evaluation_strategy: str = "hybrid"
    coordination_strategy: str = "team_first_single_pass"
    promotion_policy: str = "epoch_full_evaluation"
    full_evaluation_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrchestratorSchedulingConfig":
        """Build scheduling config from a YAML mapping."""
        config = cls(
            evaluation_strategy=str(data.get("evaluation_strategy", "hybrid")),
            coordination_strategy=str(data.get("coordination_strategy", "team_first_single_pass")),
            promotion_policy=str(data.get("promotion_policy", "epoch_full_evaluation")),
            full_evaluation_enabled=_bool_value(data.get("full_evaluation_enabled"), default=True),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Enforce the 011 scheduling strategy contract."""
        if self.evaluation_strategy != "hybrid":
            raise ValueError("scheduling.evaluation_strategy must be hybrid")
        if self.coordination_strategy != "team_first_single_pass":
            raise ValueError("scheduling.coordination_strategy must be team_first_single_pass")
        if self.promotion_policy != "epoch_full_evaluation":
            raise ValueError("scheduling.promotion_policy must be epoch_full_evaluation")


@dataclass(frozen=True, slots=True)
class AutoCoordinatingHarnessConfig:
    """Top-level orchestration configuration."""

    workspace_dir: str = ""
    max_epochs: int = 1
    freeze_team_skill: bool = False
    freeze_team_members: bool = False
    model_configs: ModelConfigs = field(default_factory=ModelConfigs)
    data_loader: DataLoaderConfig = field(default_factory=DataLoaderConfig)
    dataset_curation: DatasetCurationConfig = field(default_factory=DatasetCurationConfig)
    dataset_generator: DatasetGeneratorConfig = field(default_factory=DatasetGeneratorConfig)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    evaluation_result_analyzer: EvaluationResultAnalyzerConfig = field(default_factory=EvaluationResultAnalyzerConfig)
    team_skill_optimizer: TeamSkillOptimizerConfig = field(default_factory=TeamSkillOptimizerConfig)
    member_optimizer: MemberOptimizerConfig = field(default_factory=MemberOptimizerConfig)
    optimization_experience_learner: OptimizationExperienceLearnerConfig = field(
        default_factory=OptimizationExperienceLearnerConfig
    )
    scheduling: OrchestratorSchedulingConfig = field(default_factory=OrchestratorSchedulingConfig)
    seed_evaluation: SeedEvaluationConfig = field(default_factory=SeedEvaluationConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoCoordinatingHarnessConfig":
        """Build top-level config from a YAML mapping."""
        model_configs = ModelConfigs.from_dict(_mapping(data.get("model_configs")))
        dataset_generator = DatasetGeneratorConfig.from_dict(_mapping(data.get("dataset_generator")))
        evaluator = EvaluatorConfig.from_dict(_mapping(data.get("evaluator")))
        evaluation_result_analyzer = EvaluationResultAnalyzerConfig.from_dict(
            _mapping(data.get("evaluation_result_analyzer"))
        )
        team_skill_optimizer = TeamSkillOptimizerConfig.from_dict(_mapping(data.get("team_skill_optimizer")))
        member_optimizer = MemberOptimizerConfig.from_dict(_mapping(data.get("member_optimizer")))
        optimization_experience_learner = OptimizationExperienceLearnerConfig.from_dict(
            _mapping(data.get("optimization_experience_learner"))
        )
        model_configs = _effective_model_configs(
            model_configs=model_configs,
            dataset_generator=dataset_generator,
            evaluator=evaluator,
            evaluation_result_analyzer=evaluation_result_analyzer,
            team_skill_optimizer=team_skill_optimizer,
            member_optimizer=member_optimizer,
            optimization_experience_learner=optimization_experience_learner,
        )
        dataset_generator = replace(
            dataset_generator,
            model_config_ref=model_configs.dataset_generation,
        )
        evaluator = replace(
            evaluator,
            model_config_ref=model_configs.evaluation,
            judge_model_config_ref=model_configs.judge,
        )
        evaluation_result_analyzer = replace(
            evaluation_result_analyzer,
            model_config_ref=model_configs.analysis,
        )
        team_skill_optimizer = replace(
            team_skill_optimizer,
            model_config_ref=model_configs.team_skill_optimization,
        )
        member_optimizer = replace(
            member_optimizer,
            model_config_ref=model_configs.member_optimization,
        )
        optimization_experience_learner = replace(
            optimization_experience_learner,
            model_config_ref=model_configs.experience_learning,
        )
        config = cls(
            workspace_dir=str(data.get("workspace_dir", "")),
            max_epochs=_int_value(data.get("max_epochs"), default=1),
            freeze_team_skill=_bool_value(data.get("freeze_team_skill"), default=False),
            freeze_team_members=_bool_value(data.get("freeze_team_members"), default=False),
            model_configs=model_configs,
            data_loader=DataLoaderConfig.from_dict(_mapping(data.get("data_loader"))),
            dataset_curation=DatasetCurationConfig.from_dict(_mapping(data.get("dataset_curation"))),
            dataset_generator=dataset_generator,
            evaluator=evaluator,
            evaluation_result_analyzer=evaluation_result_analyzer,
            team_skill_optimizer=team_skill_optimizer,
            member_optimizer=member_optimizer,
            optimization_experience_learner=optimization_experience_learner,
            scheduling=OrchestratorSchedulingConfig.from_dict(_mapping(data.get("scheduling"))),
            seed_evaluation=SeedEvaluationConfig.from_dict(_mapping(data.get("seed_evaluation"))),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Enforce cross-field constraints before a run starts."""
        if self.max_epochs < 1:
            raise ValueError("max_epochs must be greater than or equal to 1")
        if self.data_loader.batch_size < 1:
            raise ValueError("data_loader.batch_size must be greater than or equal to 1")
        if not self.data_loader.batch_balance_keys:
            raise ValueError("data_loader.batch_balance_keys must not be empty")
        if self.dataset_curation.score_threshold < 0:
            raise ValueError("dataset_curation.score_threshold must be non-negative")
        if self.evaluation_result_analyzer.max_issues < 1:
            raise ValueError("evaluation_result_analyzer.max_issues must be greater than or equal to 1")
        if self.evaluation_result_analyzer.evidence_limit_per_issue < 1:
            raise ValueError("evaluation_result_analyzer.evidence_limit_per_issue must be greater than or equal to 1")
        if self.freeze_team_skill and self.freeze_team_members:
            raise ValueError("freeze_team_skill and freeze_team_members cannot both be true")


def _mapping(value: Any) -> dict[str, Any]:
    """Return ``value`` as a mapping or an empty mapping."""
    if isinstance(value, dict):
        return value
    return {}


def _effective_model_configs(
    *,
    model_configs: ModelConfigs,
    dataset_generator: DatasetGeneratorConfig,
    evaluator: EvaluatorConfig,
    evaluation_result_analyzer: EvaluationResultAnalyzerConfig,
    team_skill_optimizer: TeamSkillOptimizerConfig,
    member_optimizer: MemberOptimizerConfig,
    optimization_experience_learner: OptimizationExperienceLearnerConfig,
) -> ModelConfigs:
    """Merge top-level model config refs with legacy module-local refs."""
    return ModelConfigs(
        evaluation=model_configs.evaluation or evaluator.model_config_ref,
        judge=(
            model_configs.judge
            or evaluator.judge_model_config_ref
            or model_configs.evaluation
            or evaluator.model_config_ref
        ),
        analysis=model_configs.analysis or evaluation_result_analyzer.model_config_ref,
        team_skill_optimization=(model_configs.team_skill_optimization or team_skill_optimizer.model_config_ref),
        member_optimization=model_configs.member_optimization or member_optimizer.model_config_ref,
        experience_learning=(model_configs.experience_learning or optimization_experience_learner.model_config_ref),
        dataset_generation=model_configs.dataset_generation or dataset_generator.model_config_ref,
    )


def _string_list(value: Any, *, default: list[str] | None = None) -> list[str]:
    """Parse a YAML scalar/list into a list of strings."""
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value if item is not None]
    raise ValueError(f"expected a list of strings, got {type(value).__name__}")


def _bool_value(value: Any, *, default: bool) -> bool:
    """Parse bool-like YAML values without treating arbitrary strings as true."""
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
    """Parse an integer config value."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"expected an integer value, got {value!r}")
    return int(value)


def _float_value(value: Any, *, default: float) -> float:
    """Parse a float config value."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"expected a numeric value, got {value!r}")
    return float(value)


__all__ = [
    "AutoCoordinatingHarnessConfig",
    "DatasetCurationConfig",
    "DataLoaderConfig",
    "DatasetGeneratorConfig",
    "EvaluationResultAnalyzerConfig",
    "EvaluatorConfig",
    "MemberOptimizerConfig",
    "ModelConfigs",
    "OptimizationExperienceLearnerConfig",
    "OrchestratorSchedulingConfig",
    "SeedEvaluationConfig",
    "TeamSkillOptimizerConfig",
]
