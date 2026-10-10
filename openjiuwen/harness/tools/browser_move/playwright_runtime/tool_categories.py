# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared browser tool categories for capture and progress accounting."""

BROWSER_OBSERVATION_TOOL_NAMES = frozenset(
    {
        "browser_find",
        "browser_probe_cards",
        "browser_probe_interactives",
        "browser_snapshot",
    }
)


def is_browser_observation_tool(tool_name: str) -> bool:
    """Recognize the observation category, including qualified MCP names."""
    name = str(tool_name or "").strip().lower()
    return any(
        name == expected or name.endswith(f".{expected}") or name.endswith(f"_{expected}")
        for expected in BROWSER_OBSERVATION_TOOL_NAMES
    )
