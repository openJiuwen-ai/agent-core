# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Caller identity carried from a spawn into the child runtime tools."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


FORK_TYPE = "fork"


@dataclass
class ToolBinding:
    """Binds a nested agent's six runtime tools to the root control."""

    control: Any
    caller_id: str
    caller_depth: int
    caller_kind: str


@dataclass
class SpawnFrame:
    """Active create_subagent call, set by the root control around create."""

    caller_agent: Any
    caller_depth: int
    caller_kind: str
    caller_session_id: str | None
    child_id: str
    child_depth: int
    child_kind: str
    control: Any
    plant_seed: bool = True
    task_description: str = ""


_TOOL_BINDING: ContextVar[ToolBinding | None] = ContextVar(
    "subagent_tool_binding",
    default=None,
)
_SPAWN_FRAME: ContextVar[SpawnFrame | None] = ContextVar(
    "subagent_spawn_frame",
    default=None,
)


def current_tool_binding() -> ToolBinding | None:
    return _TOOL_BINDING.get()


def current_spawn_frame() -> SpawnFrame | None:
    return _SPAWN_FRAME.get()


def push_creation(
    frame: SpawnFrame,
    binding: ToolBinding,
) -> tuple[Token[SpawnFrame | None], Token[ToolBinding | None]]:
    return _SPAWN_FRAME.set(frame), _TOOL_BINDING.set(binding)


def pop_creation(
    frame_token: Token[SpawnFrame | None],
    binding_token: Token[ToolBinding | None],
) -> None:
    _SPAWN_FRAME.reset(frame_token)
    _TOOL_BINDING.reset(binding_token)


def runtime_rail_required(enable_runtime: bool, has_subagents: bool) -> bool:
    """Return whether create_deep_agent should mount SubagentRail.

    A fork has no child specs, but its six tools must still be registered so a
    spawn call can return the leaf error.
    """
    if has_subagents:
        return True
    binding = current_tool_binding()
    return bool(
        enable_runtime
        and binding is not None
        and binding.caller_kind == "fork"
    )
