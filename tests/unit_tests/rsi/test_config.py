# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for standalone Harness RSI configuration."""

from dataclasses import asdict
from pathlib import Path

import pytest

from openjiuwen.rsi.harness_rsi.config import (
    AutoCoordinatingHarnessConfig,
    ModelConfigs,
    OrchestratorSchedulingConfig,
    PromotionConfig,
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
                "max_issue_attempts_per_batch": 7,
                "max_repair_rounds_per_batch": 5,
                "sibling_candidate_count": 4,
                "improver_policy_ref": "policies/i1.yaml",
            },
        }
    )

    assert config.member_optimizer.freeze is False
    assert config.member_optimizer.action_group_configs == ["actions.yaml"]
    assert config.member_optimizer.agent_skills_dirs == ["skills_a", "skills_b"]
    assert config.member_optimizer.allowed_prompt_surfaces == ["prompt_section"]
    assert config.member_optimizer.adapt_frozen_team_issues is True
    assert config.member_optimizer.max_issue_attempts_per_batch == 7
    assert config.member_optimizer.max_repair_rounds_per_batch == 5
    assert config.member_optimizer.sibling_candidate_count == 4
    assert config.member_optimizer.improver_policy_ref == "policies/i1.yaml"


def test_member_optimizer_defaults_to_three_repair_rounds() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict({})

    assert config.member_optimizer.max_repair_rounds_per_batch == 3
    assert config.member_optimizer.max_issue_attempts_per_batch == 0
    assert config.member_optimizer.sibling_candidate_count == 1
    assert config.member_optimizer.improver_policy_ref == ""


def test_invalid_repair_round_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_repair_rounds_per_batch"):
        AutoCoordinatingHarnessConfig.from_dict({"member_optimizer": {"max_repair_rounds_per_batch": 0}})


def test_invalid_issue_attempt_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_issue_attempts_per_batch"):
        AutoCoordinatingHarnessConfig.from_dict({"member_optimizer": {"max_issue_attempts_per_batch": -1}})


def test_invalid_sibling_candidate_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="sibling_candidate_count"):
        AutoCoordinatingHarnessConfig.from_dict({"member_optimizer": {"sibling_candidate_count": 0}})


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
    assert config.max_epochs == 5


def test_evaluator_solver_backend_defaults_to_deep_agent() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict({})

    assert config.evaluator.solver_backend == "deep_agent"
    assert config.evaluator.jiuwenswarm_executable == ""
    assert config.evaluator.jiuwenswarm_python == ""
    assert config.evaluator.jiuwenswarm_expected_version == ""
    assert config.evaluator.jiuwenswarm_startup_timeout_sec == 120
    assert config.evaluator.jiuwenswarm_runtime_timeout_sec == 3600
    assert config.evaluator.jiuwenswarm_runtime_profile == "task86"


def test_evaluator_parses_jiuwenswarm_solver_settings() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict(
        {
            "evaluator": {
                "solver_backend": "jiuwenswarm",
                "jiuwenswarm_executable": "bin/jiuwenswarm",
                "jiuwenswarm_python": ".venv/bin/python",
                "jiuwenswarm_expected_version": "1.2.3",
                "jiuwenswarm_startup_timeout_sec": 90,
                "jiuwenswarm_runtime_timeout_sec": 2400,
                "jiuwenswarm_runtime_profile": "task86",
            }
        }
    )

    assert config.evaluator.solver_backend == "jiuwenswarm"
    assert config.evaluator.jiuwenswarm_executable == "bin/jiuwenswarm"
    assert config.evaluator.jiuwenswarm_python == ".venv/bin/python"
    assert config.evaluator.jiuwenswarm_expected_version == "1.2.3"
    assert config.evaluator.jiuwenswarm_startup_timeout_sec == 90
    assert config.evaluator.jiuwenswarm_runtime_timeout_sec == 2400
    assert config.evaluator.jiuwenswarm_runtime_profile == "task86"


def test_invalid_evaluator_solver_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="evaluator.solver_backend"):
        AutoCoordinatingHarnessConfig.from_dict({"evaluator": {"solver_backend": "unknown"}})


@pytest.mark.parametrize(
    "field",
    ["jiuwenswarm_startup_timeout_sec", "jiuwenswarm_runtime_timeout_sec"],
)
def test_invalid_jiuwenswarm_timeout_is_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        AutoCoordinatingHarnessConfig.from_dict({"evaluator": {field: 0}})


def test_invalid_scheduling_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="scheduling.evaluation_strategy"):
        AutoCoordinatingHarnessConfig.from_dict({"scheduling": {"evaluation_strategy": "batch_only"}})


def test_empty_batch_balance_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="batch_balance_keys"):
        AutoCoordinatingHarnessConfig.from_dict({"data_loader": {"batch_balance_keys": []}})


def test_promotion_defaults_match_python_yaml_and_bootstrap(tmp_path: Path) -> None:
    configs = [
        AutoCoordinatingHarnessConfig(),
        AutoCoordinatingHarnessConfig.from_dict({}),
        load_auto_coordinating_harness_config(str(tmp_path / "orchestrator.yaml")),
    ]
    for config in configs:
        assert config.promotion == PromotionConfig(policy_version="continuous_two_stage_v2")
        assert config.evaluator.score_mode == "continuous"
        assert "promotion_policy" not in asdict(config.scheduling)


def test_legacy_scheduling_policy_is_migrated_to_promotion() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict({
        "scheduling": {"promotion_policy": "epoch_full_evaluation"},
    })
    assert config.promotion.policy_version == "legacy_epoch_v1"
    assert config.evaluator.score_mode == "binary"
    assert "promotion_policy" not in asdict(config.scheduling)
    assert AutoCoordinatingHarnessConfig.from_dict(asdict(config)) == config


def test_conflicting_promotion_selectors_are_rejected() -> None:
    with pytest.raises(ValueError, match="scheduling.promotion_policy"):
        AutoCoordinatingHarnessConfig.from_dict({
            "scheduling": {"promotion_policy": "epoch_full_evaluation"},
            "promotion": {"policy_version": "continuous_two_stage_v2"},
        })


def test_explicit_legacy_policy_remains_available() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict({
        "promotion": {"policy_version": "legacy_epoch_v1"},
    })
    assert not config.promotion.continuous
    assert config.evaluator.score_mode == "binary"


def test_default_promotion_rejects_obsolete_confirmation_runs() -> None:
    with pytest.raises(ValueError, match="confirmation_runs"):
        PromotionConfig.from_dict({"confirmation_runs": 2})


def test_scheduling_parser_rejects_unmigrated_policy() -> None:
    with pytest.raises(ValueError, match="promotion.policy_version"):
        OrchestratorSchedulingConfig.from_dict({"promotion_policy": "epoch_full_evaluation"})


def test_unknown_legacy_scheduling_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="scheduling.promotion_policy"):
        AutoCoordinatingHarnessConfig.from_dict({"scheduling": {"promotion_policy": "unknown"}})


def test_legacy_migration_preserves_explicit_thresholds() -> None:
    config = AutoCoordinatingHarnessConfig.from_dict({
        "scheduling": {"promotion_policy": "epoch_full_evaluation"},
        "promotion": {"policy_version": "legacy_epoch_v1", "min_improvement": 0.1},
    })
    assert config.promotion == PromotionConfig(policy_version="legacy_epoch_v1", min_improvement=0.1)
