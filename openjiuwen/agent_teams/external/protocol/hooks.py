# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synchronous control-plane hooks for third-party agent harnesses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from openjiuwen.agent_teams.external.protocol.models import (
    JsonObject,
    JsonValue,
    freeze_json_object,
    freeze_json_value,
)


class ToolDecisionKind(str, Enum):
    """Decision returned before a tool invocation."""

    ALLOW = "allow"
    DENY = "deny"
    REWRITE = "rewrite"
    ASK = "ask"
    PROVIDER_POLICY = "provider_policy"


@dataclass(frozen=True, slots=True)
class BeforePromptContext:
    """Context supplied before an input reaches the external agent."""

    member_name: str
    session_id: str | None
    turn_id: str | None
    prompt: str


@dataclass(frozen=True, slots=True)
class BeforePromptResult:
    """Potential prompt rewrite or stop decision returned by a hook."""

    prompt: str
    continue_execution: bool = True
    reason: str | None = None
    additional_context: str | None = None


@dataclass(frozen=True, slots=True)
class BeforeToolContext:
    """Context supplied before an external agent invokes a tool."""

    member_name: str
    session_id: str | None
    turn_id: str | None
    call_id: str
    tool_name: str
    arguments: JsonObject

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolDecision:
    """Permission and optional input rewrite returned before tool execution."""

    decision: ToolDecisionKind
    reason: str | None = None
    updated_arguments: JsonObject | None = None
    additional_context: str | None = None

    def __post_init__(self) -> None:
        if self.updated_arguments is not None:
            object.__setattr__(self, "updated_arguments", freeze_json_object(self.updated_arguments))
        if self.decision is ToolDecisionKind.REWRITE and self.updated_arguments is None:
            raise ValueError("rewrite tool decision requires updated_arguments")
        if self.decision is not ToolDecisionKind.REWRITE and self.updated_arguments is not None:
            raise ValueError("updated_arguments is only valid for a rewrite tool decision")


@dataclass(frozen=True, slots=True)
class AfterToolContext:
    """Context supplied after a successful or failed tool invocation."""

    member_name: str
    session_id: str | None
    turn_id: str | None
    call_id: str
    tool_name: str
    arguments: JsonObject
    result: JsonValue = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))
        object.__setattr__(self, "result", freeze_json_value(self.result))


@dataclass(frozen=True, slots=True)
class AfterToolResult:
    """Optional tool-result rewrite and context returned by a hook."""

    replace_result: bool = False
    updated_result: JsonValue = None
    additional_context: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "updated_result", freeze_json_value(self.updated_result))


@dataclass(frozen=True, slots=True)
class StopHookContext:
    """Context supplied when a turn is about to stop."""

    member_name: str
    session_id: str | None
    turn_id: str | None
    reason: str | None = None
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_json_object(self.metadata))


@runtime_checkable
class HarnessHookDispatcher(Protocol):
    """Control-plane callbacks awaited by a harness at execution boundaries.

    Hook return values may change or stop execution. Hook lifecycle events in
    :mod:`events` are only observational mirrors and must not be used to make
    permission decisions.
    """

    async def before_prompt(self, context: BeforePromptContext) -> BeforePromptResult:
        """Inspect or rewrite an input before model execution."""
        ...

    async def before_tool(self, context: BeforeToolContext) -> ToolDecision:
        """Authorize, reject, rewrite, ask the host, or use provider policy."""
        ...

    async def after_tool(self, context: AfterToolContext) -> AfterToolResult:
        """Inspect or rewrite a tool result after execution."""
        ...

    async def on_stop(self, context: StopHookContext) -> None:
        """Observe a pending turn stop before it is finalized."""
        ...


__all__ = [
    "AfterToolContext",
    "AfterToolResult",
    "BeforePromptContext",
    "BeforePromptResult",
    "BeforeToolContext",
    "HarnessHookDispatcher",
    "StopHookContext",
    "ToolDecision",
    "ToolDecisionKind",
]
