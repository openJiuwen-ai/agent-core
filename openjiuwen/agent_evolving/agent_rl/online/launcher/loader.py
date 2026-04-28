# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""YAML loading helpers for online RL launcher runtime config."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from omegaconf import OmegaConf

from .schema import OnlineRLConfig

DEFAULT_CONFIG_FILENAME = 'online_rl_launcher.yaml'


def resolve_default_launcher_config_path() -> Path:
    yaml_path = Path(__file__).resolve().parent.parent / 'yaml' / DEFAULT_CONFIG_FILENAME
    if yaml_path.exists():
        return yaml_path.resolve()
    raise FileNotFoundError(f'Default config file not found: {yaml_path}')


def load_runtime_config(
    *,
    config_path: str | None,
    cli_overrides: dict[str, object],
) -> tuple[OnlineRLConfig, Path]:
    default_cfg_path = resolve_default_launcher_config_path()

    cfg_path = Path(config_path).expanduser().resolve() if config_path else default_cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f'Config file not found: {cfg_path}')

    schema_cfg = OmegaConf.structured(OnlineRLConfig)
    default_file_cfg = OmegaConf.load(default_cfg_path)
    layered_cfgs = [schema_cfg, default_file_cfg]
    if cfg_path != default_cfg_path:
        layered_cfgs.append(OmegaConf.load(cfg_path))
    layered_cfgs.append(OmegaConf.create(cli_overrides))
    merged = OmegaConf.merge(*layered_cfgs)
    OmegaConf.resolve(merged)
    return cast(OnlineRLConfig, OmegaConf.to_object(merged)), cfg_path
