# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Composition root for online RL/SFT runtime assembly."""

from __future__ import annotations

from typing import Any

from .rail_factory import (
    OnlineTrainingRailEnvConfig,
    build_online_rail_from_env,
    build_online_training_rail_from_env,
    build_rl_online_rail_from_env,
    has_online_training_rail,
    has_rl_online_rail,
    is_online_training_rail_instance,
    is_rl_online_rail_enabled_from_env,
    is_rl_online_rail_instance,
)


def build_training_executor(
    *,
    train_backend: str,
    base_model_path: str,
    lora_repo: Any,
    notifier: Any,
    training_gpu_ids: str,
    nproc_per_node: int = 1,
    ppo_config_path: str | None = None,
    ppo_samples_per_step: int = 0,
    evaler: Any | None = None,
    sft_rollouter: str = "multi_turn_supervisor",
    supervisor_url: str = "",
    supervisor_token: str = "",
    supervisor_model: str = "",
    target_model_id: str = "",
    sft_dry_run: bool = False,
) -> Any:
    """Build the concrete training executor selected by ``train_backend``.

    The scheduler owns orchestration and state transitions; this factory is the
    single place where that stable core binds to PPO or SFT implementation code.
    """

    normalized = (train_backend or "PPO").strip().upper()
    if normalized == "SFT":
        from ..backends.sft.trainer import SFTTrainingExecutor

        return SFTTrainingExecutor(
            base_model_path=base_model_path,
            lora_repo=lora_repo,
            notifier=notifier,
            training_gpu_ids=training_gpu_ids,
            rollouter_name=sft_rollouter,
            supervisor_url=supervisor_url,
            supervisor_token=supervisor_token,
            supervisor_model=supervisor_model,
            target_model_id=target_model_id or base_model_path,
            dry_run=sft_dry_run,
        )
    if normalized == "PPO":
        from ..backends.rl.trainer import PPOTrainingExecutor

        return PPOTrainingExecutor(
            base_model_path=base_model_path,
            lora_repo=lora_repo,
            notifier=notifier,
            nproc_per_node=nproc_per_node,
            training_gpu_ids=training_gpu_ids,
            ppo_config_path=ppo_config_path,
            ppo_samples_per_step=ppo_samples_per_step,
            evaler=evaler,
        )
    raise ValueError(f"unsupported train_backend: {train_backend}")


__all__ = [
    "OnlineTrainingRailEnvConfig",
    "build_training_executor",
    "build_online_rail_from_env",
    "build_online_training_rail_from_env",
    "build_rl_online_rail_from_env",
    "has_online_training_rail",
    "has_rl_online_rail",
    "is_online_training_rail_instance",
    "is_rl_online_rail_enabled_from_env",
    "is_rl_online_rail_instance",
]
