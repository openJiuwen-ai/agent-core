# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime launcher config merge: Python defaults + optional YAML + CLI (Pydantic validate)."""

from __future__ import annotations

from pathlib import Path

from omegaconf import OmegaConf

import openjiuwen.agent_evolving.agent_rl.config.online_config as _online_config_mod
from openjiuwen.agent_evolving.agent_rl.config.online_config import (
    BUILTIN_ONLINE_RL_CONFIG,
    OnlineRLConfig,
)

DEFAULT_CONFIG_FILENAME = "online_config.py (built-in)"


def resolve_builtin_online_config_path() -> Path:
    """Path to ``online_config.py`` shown as effective config origin when using built-in defaults."""
    path = getattr(_online_config_mod, "__file__", None)
    if isinstance(path, str):
        resolved = Path(path).resolve()
        if resolved.exists():
            return resolved
    raise RuntimeError("Cannot locate openjiuwen.agent_evolving.agent_rl.config.online_config")


def load_runtime_config(
    *,
    config_path: str | None,
    cli_overrides: dict[str, object],
) -> tuple[OnlineRLConfig, Path]:
    builtin_py = resolve_builtin_online_config_path()
    base_layer = OmegaConf.create(BUILTIN_ONLINE_RL_CONFIG)

    layered_cfgs = [base_layer]
    resolved_path: Path
    if config_path:
        resolved_path = Path(config_path).expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Config file not found: {resolved_path}")
        layered_cfgs.append(OmegaConf.load(resolved_path))
    else:
        resolved_path = builtin_py

    layered_cfgs.append(OmegaConf.create(cli_overrides))
    merged = OmegaConf.merge(*layered_cfgs)
    OmegaConf.resolve(merged)
    return OnlineRLConfig.model_validate(OmegaConf.to_container(merged, resolve=True)), resolved_path
