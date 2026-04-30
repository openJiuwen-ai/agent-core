# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Optional

from omegaconf import OmegaConf

from openjiuwen.agent_evolving.agent_rl.config.online_config import ONLINE_PPO_VERL_HYDRA_OVERLAY


def compose_online_ppo_config(
    *,
    model_path: str,
    n_gpus_per_node: int = 2,
    config_path: Optional[str] = None,
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

    if not cfg.trainer.get("default_local_dir"):
        cfg.trainer.default_local_dir = "/tmp/online_ppo_ckpt"

    OmegaConf.resolve(cfg)
    return cfg
