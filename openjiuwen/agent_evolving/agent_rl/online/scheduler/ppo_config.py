# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from typing import Optional

from omegaconf import OmegaConf

DEFAULT_PPO_CONFIG_FILENAME = 'ppo_online_trainer.yaml'


def resolve_default_ppo_config_path() -> Path:
    yaml_path = Path(__file__).resolve().parent.parent / 'yaml' / DEFAULT_PPO_CONFIG_FILENAME
    if yaml_path.exists():
        return yaml_path.resolve()
    raise FileNotFoundError(f'Default PPO config file not found: {yaml_path}')


def compose_online_ppo_config(
    *,
    model_path: str,
    n_gpus_per_node: int = 2,
    config_path: Optional[str] = None,
):
    """Build a Hydra config for online PPO training."""
    from hydra import compose, initialize_config_dir

    if config_path is None:
        cfg_dir = str(resolve_default_ppo_config_path().parent)
        config_name = "ppo_online_trainer"
    else:
        cfg_dir = str(Path(config_path).parent)
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
