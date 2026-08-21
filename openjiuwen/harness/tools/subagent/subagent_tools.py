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
from openjiuwen.harness.subagent_runtime.status_events import map_status_to_view

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


def _attach_call_timeout(card: ToolCard, timeout_s: float) -> ToolCard:
    card.properties = {
        **(card.properties if isinstance(card.properties, dict) else {}),
        "resilience": {"timeout_s": timeout_s},
    }
    return card


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


_SPAWN_REQUIRED_FIELDS = ("subagent_type", "task_description", "display_name", "role")


def _validate_spawn_payload(payload: dict[str, Any]) -> None:
    missing = [name for name in _SPAWN_REQUIRED_FIELDS if not payload.get(name)]
    if not missing:
        return
    raise build_error(
        StatusCode.TOOL_SESSION_TOOL_INVOKED,
        reason=(
            "'subagent_type', 'task_description', 'display_name', and 'role' "
            "are required"
        ),
    )


def _parse_subagent_ids(payload: dict[str, Any]) -> list[str]:
    """Normalize wait inputs; models often pass subagent_id instead of subagent_ids."""
    raw = payload.get("subagent_ids")
    if raw is None:
        raw = payload.get("subagent_id")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]


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
        display_name = payload.get("display_name")
        role = payload.get("role")
        _validate_spawn_payload(payload)

        browser_capabilities = _parse_browser_capabilities(payload, str(subagent_type))
        result = await control.spawn(
            str(subagent_type),
            str(task_description),
            display_name=str(display_name),
            role=str(role),
            browser_capabilities=browser_capabilities,
        )
        await control.emit_status_update(result.subagent_id, session=kwargs.get("session"))
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

        subagent_ids = _parse_subagent_ids(payload)
        if not subagent_ids:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'subagent_ids' must be a non-empty list",
            )

        timeout_ms = payload.get("timeout_ms", WAIT_TIMEOUT_MS_DEFAULT)
        if not isinstance(timeout_ms, int):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'timeout_ms' must be an integer",
            )

        result = await control.wait(list(subagent_ids), timeout_ms=timeout_ms)
        session = kwargs.get("session")
        for sid in subagent_ids:
            await control.emit_status_update(str(sid), session=session)
        return ToolOutput(
            success=True,
            data={
                "statuses": {
                    sid: status.kind.value for sid, status in result.statuses.items()
                },
                "results": dict(result.results),
                "output_files": dict(result.output_files),
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


class SubagentSendInputTool(Tool):
    """Send follow-up input to an existing subagent instance."""

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

        subagent_id = payload.get("subagent_id")
        query = payload.get("query")
        if not subagent_id or not isinstance(subagent_id, str):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'subagent_id' is required",
            )
        if not query or not isinstance(query, str) or not str(query).strip():
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'query' is required",
            )

        interrupt = payload.get("interrupt", False)
        if not isinstance(interrupt, bool):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'interrupt' must be a boolean",
            )

        task_id = await control.send_input(
            subagent_id,
            str(query),
            interrupt=interrupt,
        )
        session = kwargs.get("session")
        await control.emit_status_update(subagent_id, session=session)
        view = map_status_to_view(control.get_status(subagent_id))
        return ToolOutput(
            success=True,
            data={
                "subagent_id": subagent_id,
                "task_id": task_id,
                "status": view["status"],
            },
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, **kwargs)


class SubagentCloseTool(Tool):
    """Close an idle subagent instance and release its capacity slot."""

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

        subagent_id = payload.get("subagent_id")
        if not subagent_id or not isinstance(subagent_id, str):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'subagent_id' is required",
            )

        previous = await control.close(subagent_id, reason="manual")
        session = kwargs.get("session")
        await control.emit_status_update(subagent_id, session=session)
        return ToolOutput(
            success=True,
            data={
                "subagent_id": subagent_id,
                "previous_status": previous.kind.value,
            },
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, **kwargs)


class SubagentResumeTool(Tool):
    """Restore a closed or evicted subagent from checkpointer without enqueueing work."""

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

        subagent_id = payload.get("subagent_id")
        if not subagent_id or not isinstance(subagent_id, str):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="'subagent_id' is required",
            )

        result = await control.resume(subagent_id)
        session = kwargs.get("session")
        await control.emit_status_update(subagent_id, session=session)
        view = map_status_to_view(result.status)
        data: dict[str, Any] = {
            "subagent_id": subagent_id,
            "status": view["status"],
            "turn_outcome": view.get("turn_outcome"),
            "restored": result.restored,
        }
        if result.message:
            data["message"] = result.message
        return ToolOutput(success=True, data=data)

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        yield await self.invoke(inputs, **kwargs)


def build_subagent_tools(
    parent_agent: "DeepAgent",
    *,
    language: str = "cn",
    available_agents: str = "",
    agent_id: Optional[str] = None,
) -> List[Tool]:
    """Build runtime subagent tools (spawn, wait, list, send_input, close, resume)."""
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
    _attach_call_timeout(wait_card, WAIT_TIMEOUT_MS_DEFAULT / 1000.0)
    list_card = build_tool_card(
        name="subagent_list",
        tool_id="subagent_list",
        language=language,
        agent_id=agent_id,
    )
    send_input_card = build_tool_card(
        name="subagent_send_input",
        tool_id="subagent_send_input",
        language=language,
        agent_id=agent_id,
    )
    close_card = build_tool_card(
        name="subagent_close",
        tool_id="subagent_close",
        language=language,
        agent_id=agent_id,
    )
    resume_card = build_tool_card(
        name="subagent_resume",
        tool_id="subagent_resume",
        language=language,
        agent_id=agent_id,
    )
    return [
        SubagentSpawnTool(spawn_card, parent_agent, language=language),
        SubagentWaitTool(wait_card, parent_agent, language=language),
        SubagentListTool(list_card, parent_agent, language=language),
        SubagentSendInputTool(send_input_card, parent_agent, language=language),
        SubagentCloseTool(close_card, parent_agent, language=language),
        SubagentResumeTool(resume_card, parent_agent, language=language),
    ]


__all__ = [
    "SubagentCloseTool",
    "SubagentListTool",
    "SubagentResumeTool",
    "SubagentSendInputTool",
    "SubagentSpawnTool",
    "SubagentWaitTool",
    "build_subagent_tools",
]
