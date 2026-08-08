# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime subagent tools (spawn / wait / list) backed by SubagentControl."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.harness.prompts.tools import ToolCardBuildOptions, build_tool_card
from openjiuwen.harness.subagent_runtime.config import WAIT_TIMEOUT_MS_DEFAULT
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.subagent._control_registry import get_subagent_control

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


def _parse_browser_capabilities(
    inputs: dict[str, Any],
    subagent_type: str,
) -> list[str] | None:
    if str(subagent_type) != "browser_agent":
        return None
    raw_capabilities = inputs.get("browser_capabilities")
    if raw_capabilities is None:
        return []
    if isinstance(raw_capabilities, list) and all(
        isinstance(capability, str) for capability in raw_capabilities
    ):
        return list(raw_capabilities)
    raise build_error(
        StatusCode.TOOL_SESSION_TOOL_INVOKED,
        reason="'browser_capabilities' must be a list of strings",
    )


def _require_dict_inputs(inputs: Input) -> dict[str, Any]:
    if isinstance(inputs, dict):
        return inputs
    raise build_error(
        StatusCode.TOOL_SESSION_TOOL_INVOKED,
        reason=f"Invalid inputs type: {type(inputs)}",
    )


class SubagentSpawnTool(Tool):
    """Spawn a persistent subagent and enqueue the first turn without blocking."""

    def __init__(
        self,
        card: ToolCard,
        parent_agent: "DeepAgent",
        language: str = "cn",
    ) -> None:
        super().__init__(card)
        self._parent_agent = parent_agent
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        payload = _require_dict_inputs(inputs)
        control = get_subagent_control(self._parent_agent, kwargs.get("session"))

        subagent_type = payload.get("subagent_type")
        task_description = payload.get("task_description")
        if not subagent_type or not task_description:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="Both 'subagent_type' and 'task_description' are required",
            )

        browser_capabilities = _parse_browser_capabilities(payload, str(subagent_type))
        result = await control.spawn(
            str(subagent_type),
            str(task_description),
            browser_capabilities=browser_capabilities,
        )
        return ToolOutput(
            success=True,
            data={
                "subagent_id": result.subagent_id,
                "sub_session_id": result.subagent_id,
                "task_id": result.task_id,
                "status": result.status.kind.value,
            },
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, **kwargs)


class SubagentWaitTool(Tool):
    """Block until subagents reach a final status and return aggregated results."""

    def __init__(
        self,
        card: ToolCard,
        parent_agent: "DeepAgent",
        language: str = "cn",
    ) -> None:
        super().__init__(card)
        self._parent_agent = parent_agent
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        payload = _require_dict_inputs(inputs)
        control = get_subagent_control(self._parent_agent, kwargs.get("session"))

        subagent_ids = payload.get("subagent_ids")
        if not isinstance(subagent_ids, list) or not subagent_ids:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'subagent_ids' must be a non-empty list",
            )
        if not all(isinstance(item, str) for item in subagent_ids):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'subagent_ids' must contain strings",
            )

        timeout_ms = payload.get("timeout_ms", WAIT_TIMEOUT_MS_DEFAULT)
        if not isinstance(timeout_ms, int):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'timeout_ms' must be an integer",
            )

        result = await control.wait(list(subagent_ids), timeout_ms=timeout_ms)
        return ToolOutput(
            success=True,
            data={
                "statuses": {
                    sid: status.kind.value for sid, status in result.statuses.items()
                },
                "results": dict(result.results),
                "timed_out": result.timed_out,
            },
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, **kwargs)


class SubagentListTool(Tool):
    """List live subagents and current capacity for the parent session."""

    def __init__(
        self,
        card: ToolCard,
        parent_agent: "DeepAgent",
        language: str = "cn",
    ) -> None:
        super().__init__(card)
        self._parent_agent = parent_agent
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        _ = _require_dict_inputs(inputs)
        control = get_subagent_control(self._parent_agent, kwargs.get("session"))
        return ToolOutput(
            success=True,
            data={
                "capacity": control.capacity(),
                "subagents": control.describe_live(),
            },
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, **kwargs)


def build_subagent_tools(
    parent_agent: "DeepAgent",
    *,
    language: str = "cn",
    available_agents: str = "",
    agent_id: Optional[str] = None,
) -> List[Tool]:
    """Build runtime subagent tools (spawn, wait, list)."""
    format_args = {"available_agents": available_agents}
    spawn_card = build_tool_card(
        name="subagent_spawn",
        tool_id="subagent_spawn",
        language=language,
        agent_id=agent_id,
        options=ToolCardBuildOptions(format_args=format_args),
    )
    wait_card = build_tool_card(
        name="subagent_wait",
        tool_id="subagent_wait",
        language=language,
        agent_id=agent_id,
    )
    list_card = build_tool_card(
        name="subagent_list",
        tool_id="subagent_list",
        language=language,
        agent_id=agent_id,
    )
    return [
        SubagentSpawnTool(spawn_card, parent_agent, language=language),
        SubagentWaitTool(wait_card, parent_agent, language=language),
        SubagentListTool(list_card, parent_agent, language=language),
    ]


__all__ = [
    "SubagentListTool",
    "SubagentSpawnTool",
    "SubagentWaitTool",
    "build_subagent_tools",
]
