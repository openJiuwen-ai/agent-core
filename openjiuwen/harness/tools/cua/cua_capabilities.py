# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trusted cua capability catalog and task-scoped resolution.

Tool names are those of ``desktop_tools.CUA_DESKTOP_TOOL_NAMES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

CORE_CUA_CAPABILITY_NAME = "core"

# Keep this list explicit. Prefix-based matching could accidentally expose a
# newly introduced cua tool before its capability policy is reviewed.
CORE_CUA_TOOL_NAMES: tuple[str, ...] = (
    "get_cursor_position",
    "get_screen_size",
    "get_window_state",
    "list_apps",
    "list_windows",
    "zoom",
)

INPUT_CUA_TOOL_NAMES: tuple[str, ...] = (
    "click",
    "double_click",
    "drag",
    "hotkey",
    "move_cursor",
    "press_key",
    "right_click",
    "scroll",
    "type_text",
)

APP_LIFECYCLE_CUA_TOOL_NAMES: tuple[str, ...] = (
    "bring_to_front",
    "launch_app",
)

# Kept out of the default grant: the cua agent's permission rules deny kill_app,
# so it is only offered when an operator asks for it explicitly.
APP_TERMINATION_CUA_TOOL_NAMES: tuple[str, ...] = ("kill_app",)


@dataclass(frozen=True)
class CuaCapability:
    """An approved cua capability exposed to the main agent."""

    name: str
    description: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedCuaCapabilities:
    """Validated task-scoped capability and tool selection."""

    requested_names: tuple[str, ...]
    selected_names: tuple[str, ...]
    rejected_names: tuple[str, ...]
    allowed_tool_names: tuple[str, ...]


DEFAULT_CUA_CAPABILITIES: tuple[CuaCapability, ...] = (
    CuaCapability(
        name=CORE_CUA_CAPABILITY_NAME,
        description=(
            "Observe the desktop read-only: list apps and windows, and capture or zoom into a window's screenshot."
        ),
        tool_names=CORE_CUA_TOOL_NAMES,
    ),
    CuaCapability(
        name="input",
        description="Send mouse and keyboard input to desktop apps: click, drag, scroll, type, and press keys.",
        tool_names=INPUT_CUA_TOOL_NAMES,
    ),
    CuaCapability(
        name="app_lifecycle",
        description="Launch or foreground desktop applications.",
        tool_names=APP_LIFECYCLE_CUA_TOOL_NAMES,
    ),
    CuaCapability(
        name="app_termination",
        description="Force-terminate desktop applications; unsaved state is lost.",
        tool_names=APP_TERMINATION_CUA_TOOL_NAMES,
    ),
)

# Capabilities the cua agent factory grants when the caller does not request a
# specific selection. Core is always implied by the resolver.
DEFAULT_CUA_AGENT_CAPABILITY_NAMES: tuple[str, ...] = ("input", "app_lifecycle")


def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize names and preserve their first-seen order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


def _build_capability_index(
    available_capabilities: Iterable[CuaCapability],
) -> dict[str, CuaCapability]:
    """Build and validate the trusted capability catalog."""
    catalog: dict[str, CuaCapability] = {}
    for capability in available_capabilities:
        name = capability.name.strip()
        if not name:
            raise ValueError("Cua capability name cannot be empty.")
        if name in catalog:
            raise ValueError(f"Duplicate cua capability name: {name}")
        catalog[name] = capability
    if CORE_CUA_CAPABILITY_NAME not in catalog:
        raise ValueError(f"Cua capability catalog must include {CORE_CUA_CAPABILITY_NAME!r}.")
    return catalog


def resolve_cua_capabilities(
    requested_names: Iterable[str] | None,
    available_capabilities: Iterable[CuaCapability] = DEFAULT_CUA_CAPABILITIES,
) -> ResolvedCuaCapabilities:
    """Validate a main-agent selection and expand it to allowed tool names.

    The main agent is responsible for selecting capabilities from their
    descriptions. This resolver performs no task interpretation: it always adds
    the core capability, rejects unknown names, and expands approved capability
    names into a deterministic task-scoped tool allowlist.
    """
    catalog = _build_capability_index(available_capabilities)
    requested = _stable_unique(requested_names or ())

    selected: list[str] = [CORE_CUA_CAPABILITY_NAME]
    rejected: list[str] = []
    for name in requested:
        if name not in catalog:
            rejected.append(name)
            continue
        if name not in selected:
            selected.append(name)

    allowed_tool_names = _stable_unique(
        tool_name for capability_name in selected for tool_name in catalog[capability_name].tool_names
    )

    return ResolvedCuaCapabilities(
        requested_names=requested,
        selected_names=tuple(selected),
        rejected_names=tuple(rejected),
        allowed_tool_names=allowed_tool_names,
    )


__all__ = [
    "APP_LIFECYCLE_CUA_TOOL_NAMES",
    "APP_TERMINATION_CUA_TOOL_NAMES",
    "CORE_CUA_CAPABILITY_NAME",
    "CORE_CUA_TOOL_NAMES",
    "DEFAULT_CUA_AGENT_CAPABILITY_NAMES",
    "DEFAULT_CUA_CAPABILITIES",
    "INPUT_CUA_TOOL_NAMES",
    "CuaCapability",
    "ResolvedCuaCapabilities",
    "resolve_cua_capabilities",
]
