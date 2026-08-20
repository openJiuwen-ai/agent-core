# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from omegaconf import OmegaConf

from openjiuwen.agent_evolving.agent_rl.config.online_config import ONLINE_PPO_VERL_HYDRA_OVERLAY


def compose_online_ppo_config(
    *,
    model_path: str,
    n_gpus_per_node: int = 2,
    config_path: Optional[str] = None,
    init_lora_adapter_path: Optional[str] = None,
):
    """Build Hydra OmegaConf for online PPO training (built-in overlay or custom YAML Hydra compose)."""
    from hydra import compose, initialize, initialize_config_dir

    if config_path is None:
        with initialize(version_base=None, config_path="pkg://verl.trainer.config"):
            ppo_cfg = compose(config_name="ppo_trainer")
        OmegaConf.set_struct(ppo_cfg, False)
        overlay_cfg = OmegaConf.create(deepcopy(ONLINE_PPO_VERL_HYDRA_OVERLAY))
        OmegaConf.set_struct(overlay_cfg, False)
        cfg = OmegaConf.merge(ppo_cfg, overlay_cfg)
    else:
        cfg_dir = str(Path(config_path).parent.resolve())
        config_name = Path(config_path).stem
        with initialize_config_dir(config_dir=cfg_dir, version_base=None):
            cfg = compose(config_name=config_name)

    OmegaConf.set_struct(cfg, False)
    cfg.actor_rollout_ref.model.path = model_path
    cfg.trainer.n_gpus_per_node = n_gpus_per_node
    _apply_env_overrides(cfg)
    init_lora_adapter_path = (
        init_lora_adapter_path
        if init_lora_adapter_path is not None
        else os.getenv("ONLINE_RL_INIT_LORA_ADAPTER_PATH", "").strip() or None
    )
    if init_lora_adapter_path:
        cfg.actor_rollout_ref.model.lora_adapter_path = init_lora_adapter_path
    device_backend = os.getenv("ONLINE_RL_DEVICE_BACKEND", "").strip().lower()
    visible_devices_env = os.getenv("ONLINE_RL_VISIBLE_DEVICES_ENV", "").strip()
    if device_backend in {"ascend", "npu"} or visible_devices_env == "ASCEND_RT_VISIBLE_DEVICES":
        cfg.trainer.device = "npu"

    deterministic_seed = os.getenv("ONLINE_RL_DETERMINISTIC_SEED", "").strip()
    if deterministic_seed:
        seed = int(deterministic_seed)
        cfg.data.seed = seed
        cfg.actor_rollout_ref.rollout.seed = seed
        cfg.trainer.seed = seed

    fsdp_model_dtype = os.getenv("ONLINE_RL_FSDP_MODEL_DTYPE", "").strip()
    if fsdp_model_dtype:
        for role in ("actor", "ref"):
            fsdp_config = cfg.actor_rollout_ref[role].get("fsdp_config")
            if fsdp_config is not None:
                fsdp_config.model_dtype = fsdp_model_dtype
                fsdp_config.dtype = fsdp_model_dtype

    if not cfg.trainer.get("default_local_dir"):
        cfg.trainer.default_local_dir = "/tmp/online_ppo_ckpt"

    OmegaConf.resolve(cfg)
    return cfg


def _env_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return int(raw)


def _env_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return float(raw)


def _set_cfg_path(cfg, path: tuple[str, ...], value: Any) -> None:
    target = cfg
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _apply_env_override(
    cfg,
    *,
    env_name: str,
    parser,
    target_paths: tuple[tuple[str, ...], ...],
) -> None:
    raw_value = parser(env_name)
    if raw_value is None:
        return
    for path in target_paths:
        _set_cfg_path(cfg, path, raw_value)


def _apply_env_overrides(cfg) -> None:
    """Apply direct-training resource overrides without requiring a YAML file."""
    int_overrides = [
        ("ONLINE_RL_MAX_PROMPT_LENGTH", (("data", "max_prompt_length"),)),
        ("ONLINE_RL_MAX_RESPONSE_LENGTH", (("data", "max_response_length"),)),
        ("ONLINE_RL_TRAIN_BATCH_SIZE", (("data", "train_batch_size"),)),
        ("ONLINE_RL_PPO_MINI_BATCH_SIZE", (("actor_rollout_ref", "actor", "ppo_mini_batch_size"),)),
        ("ONLINE_RL_PPO_MICRO_BATCH_SIZE_PER_GPU", (("actor_rollout_ref", "actor", "ppo_micro_batch_size_per_gpu"),)),
        (
            "ONLINE_RL_SEQUENCE_PARALLEL_SIZE",
            (
                ("actor_rollout_ref", "actor", "ulysses_sequence_parallel_size"),
                ("actor_rollout_ref", "actor", "fsdp_config", "ulysses_sequence_parallel_size"),
                ("actor_rollout_ref", "ref", "ulysses_sequence_parallel_size"),
                ("actor_rollout_ref", "ref", "fsdp_config", "ulysses_sequence_parallel_size"),
                ("actor_rollout_ref", "rollout", "tensor_model_parallel_size"),
            ),
        ),
        (
            "ONLINE_RL_ACTOR_PPO_MAX_TOKEN_LEN_PER_GPU",
            (("actor_rollout_ref", "actor", "ppo_max_token_len_per_gpu"),),
        ),
        (
            "ONLINE_RL_LOG_PROB_MICRO_BATCH_SIZE_PER_GPU",
            (
                ("actor_rollout_ref", "ref", "log_prob_micro_batch_size_per_gpu"),
                ("actor_rollout_ref", "rollout", "log_prob_micro_batch_size_per_gpu"),
            ),
        ),
        (
            "ONLINE_RL_REF_LOG_PROB_MAX_TOKEN_LEN_PER_GPU",
            (("actor_rollout_ref", "ref", "log_prob_max_token_len_per_gpu"),),
        ),
        (
            "ONLINE_RL_ROLLOUT_LOG_PROB_MAX_TOKEN_LEN_PER_GPU",
            (("actor_rollout_ref", "rollout", "log_prob_max_token_len_per_gpu"),),
        ),
        ("ONLINE_RL_ROLLOUT_MAX_MODEL_LEN", (("actor_rollout_ref", "rollout", "max_model_len"),)),
        ("ONLINE_RL_ROLLOUT_MAX_NUM_BATCHED_TOKENS", (("actor_rollout_ref", "rollout", "max_num_batched_tokens"),)),
    ]
    float_overrides = [
        ("ONLINE_RL_ROLLOUT_GPU_MEMORY_UTILIZATION", (("actor_rollout_ref", "rollout", "gpu_memory_utilization"),)),
    ]

    for env_name, target_paths in int_overrides:
        _apply_env_override(cfg, env_name=env_name, parser=_env_int, target_paths=target_paths)

    for env_name, target_paths in float_overrides:
        _apply_env_override(cfg, env_name=env_name, parser=_env_float, target_paths=target_paths)
