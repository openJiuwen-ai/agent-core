# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Small helpers for AgentTeam plan-mode configuration.

Team-level planning now reuses the normal DeepAgent plan-mode workflow:
the real Leader session starts in ``plan`` mode and the Leader calls
``enter_plan_mode`` / ``exit_plan_mode`` like a single code agent.
"""

from __future__ import annotations

from typing import Any


def _get_field(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def is_team_plan_enabled(spec: Any) -> bool:
    """Return whether the Leader should start in plan mode."""
    return bool(_get_field(spec, "enable_team_plan", default=False))


__all__ = ["is_team_plan_enabled"]
