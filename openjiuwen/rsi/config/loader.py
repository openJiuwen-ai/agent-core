# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configuration loading entry points."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from openjiuwen.core.common.logging import logger
from openjiuwen.rsi.config.config import AutoCoordinatingHarnessConfig

_DEFAULT_CONFIG_TEMPLATE = Path(__file__).resolve().parent.parent / "resource" / "orchestrating.default.yaml"


def _bootstrap_default_config(path: Path) -> bool:
    """Write the bundled default config template to ``path`` when it is missing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _DEFAULT_CONFIG_TEMPLATE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return True
    except Exception:
        logger.warning(
            "[auto_coordinating_harness] failed to bootstrap default config to {}",
            path,
            exc_info=True,
        )
        return False


def load_auto_coordinating_harness_config(config_path: str) -> AutoCoordinatingHarnessConfig:
    """Load and validate ``orchestrator.yaml`` from ``config_path``."""
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        _bootstrap_default_config(path)
    if not path.is_file():
        raise FileNotFoundError(f"auto-coordinating harness config not found: {path}")

    with open(path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"auto-coordinating harness config must be a mapping: {path}")

    config = AutoCoordinatingHarnessConfig.from_dict(data)
    if config.evaluator.team_spec_config_ref:
        team_spec_path = Path(config.evaluator.team_spec_config_ref).expanduser()
        if not team_spec_path.is_absolute():
            team_spec_path = path.parent / team_spec_path
        config = replace(
            config,
            evaluator=replace(
                config.evaluator,
                team_spec_config_ref=str(team_spec_path.resolve()),
            ),
        )
    return config


__all__ = [
    "load_auto_coordinating_harness_config",
]
