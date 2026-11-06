# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Builtin ``ops.tool.call`` and ``ops.tool.check`` bound to the session's active DeepAgent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events.builtins.event_types.tool_call import TOOL_CALL, ToolCallEventOutput
from openjiuwen.core.events.builtins.event_types.tool_check import TOOL_CHECK, ToolCheckOutput
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel, SessionContext
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import with_session
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult


def _react_agent() -> ReActAgent:
    agent = ReActAgent(card=AgentCard(id="ops-tool-agent"))
    agent.configure(ReActAgentConfig(model_name="fake", on_max_iterations="return_observations"))
    card = ToolCard(
        id="echo",
        name="echo",
        description="echo desc",
        input_params={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )

    async def _echo(text: str) -> dict[str, str]:
        return {"tool": "echo", "text": text}

    tool = LocalFunction(card=card, func=_echo)
    agent.ability_manager.add_ability(tool.card, tool)
    return agent


def _deep_agent(react_agent: ReActAgent | None, rails: list | None = None) -> MagicMock:
    deep = MagicMock(spec=DeepAgent)
    deep.react_agent = react_agent
    deep.find_rails_by_type.return_value = rails or []
    return deep


def _deny_rail() -> PermissionInterruptRail:
    rail = PermissionInterruptRail(config={"enabled": True, "tools": {"bash": "ask"}}, host=ToolPermissionHost())
    rail._engine.check_permission = AsyncMock(
        return_value=PermissionResult(permission=PermissionLevel.DENY, matched_rule="r1", reason="nope")
    )
    return rail


async def _ops(session_context) -> tuple[ExtensionEventBus, EventHandlerOps]:
    bus = ExtensionEventBus()
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus, builder.build(session_context=session_context)


@pytest.mark.asyncio
async def test_ops_tool_call_runs_one_tool_through_single_item_batch() -> None:
    seen: list = []

    async def capture(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        seen.append(event.event_input)
        return await event_next(event)

    session = create_agent_session(session_id="ops-tool-call-session")
    react_agent = _react_agent()
    deep = _deep_agent(react_agent)
    get_agent_runtime().bind(session.get_session_id(), deep)
    await Runner.start()
    try:
        bus, ops = await _ops(SessionContext(session.get_session_id()))
        bus.register_event_handler(TOOL_CALL, capture, {}, HandlerLevel.USER)

        @with_session(session)
        async def invoke() -> ToolCallEventOutput:
            return await ops.tool.call("echo", {"text": "a"})

        output = await invoke()
    finally:
        react_agent.ability_manager.teardown_tools()
        await Runner.stop()

    assert len(seen) == 1 and len(seen[0]) == 1
    item = seen[0][0]
    assert item.tool_name == "echo" and item.tool_call_id
    assert isinstance(output, ToolCallEventOutput)
    assert output.tool_call_id == item.tool_call_id
    assert output.result == {"tool": "echo", "text": "a"}
    assert isinstance(output.text, str) and "echo" in output.text


@pytest.mark.asyncio
@pytest.mark.parametrize("with_rail", [False, True], ids=["no-rail", "permission-rail"])
async def test_ops_tool_check_always_triggers_event_with_complete_context(with_rail: bool) -> None:
    rail = _deny_rail() if with_rail else None
    session = create_agent_session(session_id=f"ops-tool-check-session-{with_rail}")
    deep = _deep_agent(_react_agent(), [rail] if rail else [])
    get_agent_runtime().bind(session.get_session_id(), deep)

    bus, ops = await _ops(SessionContext(session.get_session_id()))
    with patch.object(bus, "trigger", wraps=bus.trigger) as trigger:
        output = await ops.tool.check("bash", {"command": "ls"})

    if not with_rail:
        assert output == ToolCheckOutput(decision="allow")
    else:
        assert output == ToolCheckOutput(decision="deny", reason="nope")
    assert trigger.await_count == 1
    assert trigger.await_args.args[0].event_type == TOOL_CHECK
    event_input = trigger.await_args.args[0].event_input
    callback_ctx = trigger.await_args.kwargs["trigger_context"].callback_ctx
    assert event_input.tool_call_id is None
    assert event_input.tool_name == "bash"
    assert isinstance(callback_ctx.inputs, ToolCallInputs)
    assert callback_ctx.inputs.tool_call.name == "bash"
    assert callback_ctx.inputs.tool_args == {"command": "ls"}
    assert callback_ctx.config is callback_ctx.agent.config
    assert callback_ctx.session is None
    assert callback_ctx.context is None
    assert callback_ctx.extra["session_id"] == session.get_session_id()
    if rail is not None:
        assert rail._engine.check_permission.await_args.kwargs["tool_args"] == {"command": "ls"}


@pytest.mark.asyncio
async def test_ops_tool_check_handler_can_decide_without_permission_rail() -> None:
    async def deny(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return ToolCheckOutput(decision="deny", reason="blocked")

    session = create_agent_session(session_id="ops-tool-check-no-rail-handler")
    deep = _deep_agent(_react_agent())
    get_agent_runtime().bind(session.get_session_id(), deep)
    bus, ops = await _ops(SessionContext(session.get_session_id()))
    bus.register_event_handler(TOOL_CHECK, deny, {}, HandlerLevel.USER)

    output = await ops.tool.check("bash", {"command": "ls"})

    assert output == ToolCheckOutput(decision="deny", reason="blocked")


@pytest.mark.asyncio
async def test_ops_tool_check_rejects_reentry_from_tool_check_handler() -> None:
    errors: list[BaseError] = []

    async def reenter(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        try:
            await ops.tool.check("bash", {"command": "ls"})
        except BaseError as exc:
            errors.append(exc)
        return await event_next(event)

    rail = _deny_rail()
    session = create_agent_session(session_id="ops-tool-check-reentry-session")
    deep = _deep_agent(_react_agent(), [rail])
    get_agent_runtime().bind(session.get_session_id(), deep)
    bus, ops = await _ops(SessionContext(session.get_session_id()))
    bus.register_event_handler(TOOL_CHECK, reenter, {}, HandlerLevel.USER)

    output = await ops.tool.check("bash", {"command": "ls"})

    assert len(errors) == 1
    assert output.decision == "deny"
    assert rail._engine.check_permission.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["call", "check"])
@pytest.mark.parametrize("case", ["unbound", "not-deep-agent", "react-agent-missing"])
async def test_ops_tool_requires_active_deep_agent(case: str, method: str) -> None:
    session = create_agent_session(session_id=f"ops-tool-error-{case}-{method}")
    runtime = get_agent_runtime()
    bound = {
        "unbound": None,
        "not-deep-agent": _react_agent(),
        "react-agent-missing": _deep_agent(None),
    }[case]
    if bound is not None:
        runtime.bind(session.get_session_id(), bound)
    bus, ops = await _ops(SessionContext(session.get_session_id()))

    with patch.object(bus, "trigger", wraps=bus.trigger) as trigger:
        with pytest.raises(BaseError):
            await getattr(ops.tool, method)("echo", {"text": "a"})

    assert trigger.await_count == 0


@pytest.mark.asyncio
async def test_ops_tool_call_requires_a_live_session_at_execution() -> None:
    session = create_agent_session(session_id="ops-tool-call-no-live-session")
    react_agent = _react_agent()
    deep = _deep_agent(react_agent)
    get_agent_runtime().bind(session.get_session_id(), deep)
    bus, ops = await _ops(SessionContext(session.get_session_id()))

    with patch.object(bus, "trigger", wraps=bus.trigger) as trigger:
        with pytest.raises(BaseError):
            await ops.tool.call("echo", {"text": "a"})

    assert trigger.await_count == 1
