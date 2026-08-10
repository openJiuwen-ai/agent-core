# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for standalone Harness RSI configuration."""

from pathlib import Path

import pytest

from openjiuwen.rsi.config import (
    AutoCoordinatingHarnessConfig,
    ModelConfigs,
    load_auto_coordinating_harness_config,
)


def test_config_parses_member_optimizer_lists_and_booleans() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "workspace_dir": "workspace",
            "member_optimizer": {
                "freeze": "off",
                "action_group_configs": "actions.yaml",
                "agent_skills_dirs": ["skills_a", "skills_b"],
                "allowed_prompt_surfaces": "prompt_section",
                "adapt_frozen_team_issues": "yes",
            },
        }
    )

    assert config.member_optimizer.freeze is False
    assert config.member_optimizer.action_group_configs == ["actions.yaml"]
    assert config.member_optimizer.agent_skills_dirs == ["skills_a", "skills_b"]
    assert config.member_optimizer.allowed_prompt_surfaces == ["prompt_section"]
    assert config.member_optimizer.adapt_frozen_team_issues is True


def test_model_configs_project_to_runtime_stages() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "model_configs": {
                "evaluation": "models/evaluation.yaml",
                "analysis": "models/analysis.yaml",
                "member_optimization": "models/member.yaml",
            }
        }
    )

    assert config.model_configs == ModelConfigs(
        evaluation="models/evaluation.yaml",
        analysis="models/analysis.yaml",
        member_optimization="models/member.yaml",
    )
    assert config.evaluator.model_config_ref == "models/evaluation.yaml"
    assert config.evaluation_result_analyzer.model_config_ref == "models/analysis.yaml"
    assert config.member_optimizer.model_config_ref == "models/member.yaml"


def test_module_model_refs_remain_backward_compatible() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "evaluator": {"model_config_ref": "legacy/evaluation.yaml"},
            "evaluation_result_analyzer": {"model_config_ref": "legacy/analysis.yaml"},
            "member_optimizer": {"model_config_ref": "legacy/member.yaml"},
        }
    )

    assert config.model_configs == ModelConfigs(
        evaluation="legacy/evaluation.yaml",
        analysis="legacy/analysis.yaml",
        member_optimization="legacy/member.yaml",
    )


def test_missing_config_bootstraps_standalone_template(tmp_path: Path) -> None:
    config_path = tmp_path / "rsi" / "config.yaml"
    config = load_auto_coordinating_harness_config(str(config_path))

    assert config_path.is_file()
    assert config.evaluator.backend == "single_harness"
    assert config.evaluator.evaluation_method == "script-based"
    assert config.max_epochs == 1


def test_invalid_scheduling_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="scheduling.evaluation_strategy"):
        AutoCoordinatingHarnessConfig.from_dict({"scheduling": {"evaluation_strategy": "batch_only"}})


def test_empty_batch_balance_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="batch_balance_keys"):
        AutoCoordinatingHarnessConfig.from_dict({"data_loader": {"batch_balance_keys": []}})
