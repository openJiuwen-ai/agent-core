# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for auto-coordinating harness config parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.rsi.config import (
    AutoCoordinatingHarnessConfig,
    EvaluatorConfig,
    ModelConfigs,
    load_auto_coordinating_harness_config,
)


def test_config_parses_string_booleans_and_scalar_lists() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "freeze_team_skill": "false",
            "freeze_team_members": "no",
            "dataset_generator": {
                "coverage_dimensions": "reasoning",
            },
            "member_optimizer": {
                "freeze": "off",
                "action_group_configs": "actions.yaml",
                "agent_skills_dirs": "optimizer_skills",
                "allowed_prompt_surfaces": "prompt_section",
                "adapt_frozen_team_issues": "yes",
            },
            "team_skill_optimizer": {
                "language": "en",
                "auto_approve": "yes",
            },
            "optimization_experience_learner": {
                "enabled": "yes",
            },
        }
    )

    assert config.freeze_team_skill is False
    assert config.freeze_team_members is False
    assert config.dataset_generator.coverage_dimensions == ["reasoning"]
    assert config.member_optimizer.freeze is False
    assert config.member_optimizer.action_group_configs == ["actions.yaml"]
    assert config.member_optimizer.agent_skills_dirs == ["optimizer_skills"]
    assert config.member_optimizer.allowed_prompt_surfaces == ["prompt_section"]
    assert config.member_optimizer.adapt_frozen_team_issues is True
    assert config.team_skill_optimizer.language == "en"
    assert config.team_skill_optimizer.auto_approve is True
    assert config.optimization_experience_learner.enabled is True
    assert config.scheduling.evaluation_strategy == "hybrid"
    assert config.scheduling.coordination_strategy == "team_first_single_pass"
    assert config.scheduling.promotion_policy == "epoch_full_evaluation"
    assert config.seed_evaluation.enabled is False
    assert config.data_loader.batch_balance_keys == [
        "dimension",
        "difficulty",
        "source",
        "task_type",
    ]


def test_config_parses_member_optimizer_agent_skills_dirs_list() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "member_optimizer": {
                "agent_skills_dirs": ["skills_a", "skills_b"],
            },
        }
    )

    assert config.member_optimizer.agent_skills_dirs == ["skills_a", "skills_b"]


def test_config_parses_valid_scheduling_strategy() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "scheduling": {
                "evaluation_strategy": "hybrid",
                "coordination_strategy": "team_first_single_pass",
                "promotion_policy": "epoch_full_evaluation",
            },
        }
    )

    assert config.scheduling.evaluation_strategy == "hybrid"
    assert config.scheduling.coordination_strategy == "team_first_single_pass"
    assert config.scheduling.promotion_policy == "epoch_full_evaluation"


def test_config_parses_seed_evaluation_gate() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "seed_evaluation": {
                "enabled": "true",
                "pass_threshold": 0.75,
                "excellent_threshold": 0.98,
                "max_cases": 5,
            },
        }
    )

    assert config.seed_evaluation.enabled is True
    assert config.seed_evaluation.pass_threshold == 0.75
    assert config.seed_evaluation.excellent_threshold == 0.98
    assert config.seed_evaluation.max_cases == 5


def test_config_parses_evaluator_case_lifecycle_timeout() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "evaluator": {
                "case_lifecycle_timeout_sec": "7200",
            },
        }
    )

    assert config.evaluator.case_lifecycle_timeout_sec == 7200.0


def test_load_auto_coordinating_harness_config_bootstraps_missing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "auto-coordinating" / "config.yaml"

    config = load_auto_coordinating_harness_config(str(config_path))

    assert config_path.is_file()
    assert config.max_epochs == 1
    assert config.evaluator.evaluation_method == "exact_match"
    assert config.evaluator.case_lifecycle_timeout_sec == EvaluatorConfig().case_lifecycle_timeout_sec


def test_config_parses_model_configs_and_projects_to_modules() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "model_configs": {
                "evaluation": "models/evaluation.yaml",
                "judge": "models/judge.yaml",
                "analysis": "models/analysis.yaml",
                "team_skill_optimization": "models/team_skill.yaml",
                "member_optimization": "models/member.yaml",
                "experience_learning": "models/experience.yaml",
                "dataset_generation": "models/dataset.yaml",
            },
        }
    )

    assert config.model_configs == ModelConfigs(
        evaluation="models/evaluation.yaml",
        judge="models/judge.yaml",
        analysis="models/analysis.yaml",
        team_skill_optimization="models/team_skill.yaml",
        member_optimization="models/member.yaml",
        experience_learning="models/experience.yaml",
        dataset_generation="models/dataset.yaml",
    )
    assert config.evaluator.model_config_ref == "models/evaluation.yaml"
    assert config.evaluator.judge_model_config_ref == "models/judge.yaml"
    assert config.evaluation_result_analyzer.model_config_ref == "models/analysis.yaml"
    assert config.team_skill_optimizer.model_config_ref == "models/team_skill.yaml"
    assert config.member_optimizer.model_config_ref == "models/member.yaml"
    assert config.optimization_experience_learner.model_config_ref == "models/experience.yaml"
    assert config.dataset_generator.model_config_ref == "models/dataset.yaml"


def test_config_preserves_legacy_module_model_refs_when_top_level_is_missing() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "evaluator": {
                "model_config_ref": "legacy/evaluation.yaml",
                "judge_model_config_ref": "legacy/judge.yaml",
            },
            "dataset_generator": {"model_config_ref": "legacy/dataset.yaml"},
            "evaluation_result_analyzer": {"model_config_ref": "legacy/analysis.yaml"},
            "team_skill_optimizer": {"model_config_ref": "legacy/team_skill.yaml"},
            "member_optimizer": {"model_config_ref": "legacy/member.yaml"},
            "optimization_experience_learner": {"model_config_ref": "legacy/experience.yaml"},
        }
    )

    assert config.model_configs.evaluation == "legacy/evaluation.yaml"
    assert config.model_configs.judge == "legacy/judge.yaml"
    assert config.model_configs.dataset_generation == "legacy/dataset.yaml"
    assert config.model_configs.analysis == "legacy/analysis.yaml"
    assert config.model_configs.team_skill_optimization == "legacy/team_skill.yaml"
    assert config.model_configs.member_optimization == "legacy/member.yaml"
    assert config.model_configs.experience_learning == "legacy/experience.yaml"


def test_config_rejects_ambiguous_boolean_strings() -> None:
    with pytest.raises(ValueError, match="boolean"):
        AutoCoordinatingHarnessConfig.from_dict(
            {
                "workspace_dir": "workspace",
                "freeze_team_skill": "disabled",
            }
        )


def test_config_rejects_unknown_scheduling_strategy() -> None:
    with pytest.raises(ValueError, match="scheduling.evaluation_strategy"):
        AutoCoordinatingHarnessConfig.from_dict(
            {
                "workspace_dir": "workspace",
                "scheduling": {
                    "evaluation_strategy": "batch_only",
                },
            }
        )


def test_config_rejects_empty_batch_balance_keys() -> None:
    with pytest.raises(ValueError, match="batch_balance_keys"):
        AutoCoordinatingHarnessConfig.from_dict(
            {
                "workspace_dir": "workspace",
                "data_loader": {
                    "batch_balance_keys": [],
                },
            }
        )


def test_config_rejects_freezing_both_team_skill_and_members() -> None:
    with pytest.raises(ValueError, match="cannot both be true"):
        AutoCoordinatingHarnessConfig.from_dict(
            {
                "workspace_dir": "workspace",
                "freeze_team_skill": True,
                "freeze_team_members": True,
            }
        )
