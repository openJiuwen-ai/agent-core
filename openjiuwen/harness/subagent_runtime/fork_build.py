# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Build a fork subagent from the caller's live agent."""

from __future__ import annotations

from typing import Any, Callable

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagent_runtime.binding import FORK_TYPE, SpawnFrame
from openjiuwen.harness.subagent_runtime.fork_rail import ForkInstructionRail
from openjiuwen.harness.subagent_runtime.seed import finished_seed

_RUNTIME_TOOL_NAMES = frozenset({
    "subagent_spawn",
    "subagent_wait",
    "subagent_list",
    "subagent_send_input",
    "subagent_close",
    "subagent_resume",
    "task_tool",
    "sessions_spawn",
    "sessions_list",
    "sessions_cancel",
})

_DROP_RAIL_NAMES = frozenset({
    "ProgressiveToolRail",
    "ForkInstructionRail",
    "BrowserTaskPromptRail",
})

_inherited_tool_filter: Callable[[list[Any]], list[Any]] | None = None


def register_inherited_tool_filter(
    tool_filter: Callable[[list[Any]], list[Any]] | None,
) -> None:
    """Install the product filter that strips leader-only tools from a fork."""
    global _inherited_tool_filter
    _inherited_tool_filter = tool_filter


def fork_placeholder_spec(language: str = "cn") -> SubAgentConfig:
    """Spec that lets a delegating agent advertise fork and nothing else."""
    if language == "en":
        description = (
            "Inherits the caller's finished conversation, system prompt, tools, "
            "and model. Cannot spawn further subagents. Spawn independent forks "
            "in one message, then subagent_wait those ids."
        )
    else:
        description = (
            "继承调用方已经结束的对话、系统提示、工具和模型。不能再派子代理。"
            "多个互不依赖的 fork 放在同一条消息里一起派，然后 subagent_wait。"
        )
    return SubAgentConfig(
        agent_card=AgentCard(name=FORK_TYPE, description=description),
        system_prompt="",
        can_delegate=False,
    )


def nested_runtime_overrides(
    spec: Any,
    *,
    child_depth: int,
    max_spawn_depth: int,
    language: str = "cn",
) -> dict[str, Any]:
    """Runtime flags for a child created under the root control."""
    can_delegate = bool(getattr(spec, "can_delegate", False))
    if can_delegate and child_depth < max_spawn_depth:
        return {
            "subagents": [fork_placeholder_spec(language)],
            "enable_async_subagent": False,
            "enable_subagent_runtime": True,
            "add_general_purpose_agent": False,
        }
    return {
        "subagents": None,
        "enable_async_subagent": False,
        "enable_subagent_runtime": False,
        "add_general_purpose_agent": False,
    }


def _tool_name(tool: Any) -> str:
    if isinstance(tool, str):
        return tool.strip()
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    card = getattr(tool, "card", None)
    card_name = getattr(card, "name", None) if card is not None else None
    if isinstance(card_name, str) and card_name.strip():
        return card_name.strip()
    return ""


def _copy_tools(caller: Any) -> list[Any]:
    config = getattr(caller, "deep_config", None)
    tools = list(getattr(config, "tools", None) or [])
    kept = [tool for tool in tools if _tool_name(tool) not in _RUNTIME_TOOL_NAMES]
    if _inherited_tool_filter is None:
        return kept
    filtered = _inherited_tool_filter(kept)
    return list(filtered or [])


def _copy_rails(caller: Any, *, task_description: str, seed: list[Any]) -> list[Any]:
    configured = getattr(caller, "configured_rails", None)
    source = list(configured()) if callable(configured) else []
    kept: list[Any] = []
    for rail in source:
        if isinstance(rail, SubagentRail):
            continue
        if type(rail).__name__ in _DROP_RAIL_NAMES:
            continue
        if getattr(rail, "inherit_to_subagents", True) is False:
            continue
        fork_for_agent = getattr(rail, "fork_for_agent", None)
        kept.append(fork_for_agent() if callable(fork_for_agent) else rail)
    if not any(isinstance(rail, SysOperationRail) for rail in kept):
        kept.insert(0, SysOperationRail())
    kept.append(ForkInstructionRail(task_description, seed))
    return kept


def _caller_messages(caller: Any, session_id: str | None) -> list[Any]:
    getter = getattr(caller, "get_current_context", None)
    if not callable(getter) or not session_id:
        return []
    try:
        messages = getter(session_id)
    except Exception:
        return []
    if not isinstance(messages, list):
        return []
    return messages


def build_fork_agent(frame: SpawnFrame, task_description: str) -> Any:
    """Create the fork DeepAgent. The root control owns its runtime tools."""
    from openjiuwen.harness.factory import create_deep_agent

    caller = frame.caller_agent
    config = getattr(caller, "deep_config", None)
    seed = finished_seed(_caller_messages(caller, frame.caller_session_id)) if frame.plant_seed else []
    workspace = getattr(config, "workspace", None)
    sys_operation = getattr(config, "sys_operation", None)
    if workspace is None or sys_operation is None:
        workspace = None
        sys_operation = None
    language = getattr(config, "language", None) or "cn"
    return create_deep_agent(
        model=getattr(config, "model", None),
        card=AgentCard(id=frame.child_id, name=FORK_TYPE, description="fork"),
        system_prompt=getattr(config, "system_prompt", None) or "",
        tools=_copy_tools(caller),
        mcps=list(getattr(config, "mcps", None) or []),
        rails=_copy_rails(caller, task_description=task_description, seed=seed),
        skills=getattr(config, "skills", None),
        workspace=workspace,
        sys_operation=sys_operation,
        language=language,
        prompt_mode=getattr(config, "prompt_mode", None),
        max_iterations=getattr(config, "max_iterations", None) or 15,
        enable_task_loop=bool(getattr(config, "enable_task_loop", False)),
        enable_subagent_runtime=True,
        subagents=None,
        add_general_purpose_agent=False,
        parallel_tool_calls=bool(getattr(config, "parallel_tool_calls", True)),
        restrict_to_work_dir=bool(getattr(config, "restrict_to_work_dir", True)),
        enable_read_image_multimodal=getattr(config, "enable_read_image_multimodal", None),
        backend=getattr(config, "backend", None),
    )
