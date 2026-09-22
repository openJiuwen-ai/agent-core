# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill_train ReflACT offline skill training integrated into agent_evolving."""

from openjiuwen.agent_evolving.skill_train.config import SkillTrainConfig
from openjiuwen.agent_evolving.skill_train.launch import (
    SUPPORTED_ENVS,
    EnvPreset,
    ResolvedLaunch,
    TrainLaunchOptions,
    build_train_config,
    env_presets,
    run_offline_eval,
    run_offline_training,
)
from openjiuwen.agent_evolving.skill_train.model_compat import (
    EXEC_TARGET_BACKENDS,
    SUPPORTED_TARGET_BACKENDS,
    get_target_backend,
    is_target_exec_backend,
    set_target_backend,
)
from openjiuwen.agent_evolving.skill_train.registry import get_env_adapter
from openjiuwen.agent_evolving.skill_train.sleep import (
    AdoptResult,
    CycleOutcome,
    SleepConfig,
    adopt_all_staged_skills,
    adopt_staged_skill,
    adopt_staged_skill_async,
    run_sleep_cycle,
)
from openjiuwen.agent_evolving.skill_train.trainer import SkillReflACTTrainer, SkillTrainResult

__all__ = [
    "SkillTrainConfig",
    "SkillReflACTTrainer",
    "SkillTrainResult",
    "get_env_adapter",
    "SleepConfig",
    "CycleOutcome",
    "AdoptResult",
    "run_sleep_cycle",
    "adopt_staged_skill",
    "adopt_staged_skill_async",
    "adopt_all_staged_skills",
    # launch API (used by `jiuwenswarm skill-train` and the repo example)
    "SUPPORTED_ENVS",
    "EnvPreset",
    "ResolvedLaunch",
    "TrainLaunchOptions",
    "build_train_config",
    "env_presets",
    "run_offline_eval",
    "run_offline_training",
    # target backend selection
    "EXEC_TARGET_BACKENDS",
    "SUPPORTED_TARGET_BACKENDS",
    "get_target_backend",
    "is_target_exec_backend",
    "set_target_backend",
]
