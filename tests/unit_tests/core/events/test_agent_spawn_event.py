# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Contract and Ops integration tests for ``agent.spawn``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOps,
    EventHandlerOpsBuilder,
    EventNext,
    ExtensionEventBus,
)
from openjiuwen.core.events.builtins.event_types.agent_spawn import (
    AGENT_SPAWN,
    AgentSpawnInput,
    AgentSpawnOutput,
    AgentSpawnTriggerContext,
    prepare_agent_spawn_input,
)
from openjiuwen.core.session import with_session
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.subagent_runtime.models import SpawnResult, SubagentStatus


async def _build_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    result = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(result, EventHandlerOpsBuilder)
    return bus


def _spawn_result() -> SpawnResult:
    return SpawnResult(
        subagent_id="parent_sub_explore",
        task_id="task-1",
        status=SubagentStatus.pending_init(),
    )


def _request(**changes: str) -> AgentSpawnInput:
    values = {
        "subagent_type": "explore_agent",
        "task_description": "inspect the repository",
        "display_name": "Explorer",
        "role": "researcher",
    }
    values.update(changes)
    return AgentSpawnInput(**values)


def test_agent_spawn_input_is_normalized_and_rejects_empty_fields() -> None:
    assert prepare_agent_spawn_input(
        _request(
            subagent_type=" explore_agent ",
            task_description=" inspect the repository ",
            display_name=" Explorer ",
            role=" researcher ",
        )
    ) == _request()

    with pytest.raises(ValidationError) as invalid:
        prepare_agent_spawn_input(_request(role=" "))
    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID


@pytest.mark.asyncio
async def test_agent_spawn_core_executes_once_and_keeps_raw_result_private() -> None:
    bus = await _build_bus()
    result = _spawn_result()
    control = SimpleNamespace(
        spawn=AsyncMock(return_value=result),
        emit_status_update=AsyncMock(),
    )
    context = AgentSpawnTriggerContext(
        control=control,
        allowed_subagent_types=frozenset({"explore_agent"}),
        browser_capabilities=["navigate"],
    )

    output = await bus.trigger(
        Event(AGENT_SPAWN, _request()),
        trigger_context=context,
    )

    assert output == AgentSpawnOutput(
        subagent_id=result.subagent_id,
        status=result.status.kind.value,
    )
    assert context.raw_result is result
    control.spawn.assert_awaited_once_with(
        "explore_agent",
        "inspect the repository",
        display_name="Explorer",
        role="researcher",
        browser_capabilities=["navigate"],
    )
    control.emit_status_update.assert_awaited_once_with(result.subagent_id)


@pytest.mark.asyncio
async def test_agent_spawn_core_rechecks_rewritten_type_before_side_effect() -> None:
    bus = await _build_bus()
    control = SimpleNamespace(spawn=AsyncMock(), emit_status_update=AsyncMock())
    context = AgentSpawnTriggerContext(
        control=control,
        allowed_subagent_types=frozenset({"explore_agent"}),
    )

    with pytest.raises(ValidationError) as invalid:
        await bus.trigger(
            Event(AGENT_SPAWN, _request(subagent_type="browser_agent")),
            trigger_context=context,
        )

    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID
    control.spawn.assert_not_awaited()
    control.emit_status_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_spawn_ops_uses_bound_agent_and_real_session() -> None:
    session = Session(session_id="agent-spawn-ops-session")
    agent = DeepAgent(
        AgentCard(id="parent-agent", name="parent-agent", description="parent"),
    ).configure(DeepAgentConfig(enable_task_loop=False))
    get_agent_runtime().bind(session.get_session_id(), agent)
    bus = await _build_bus()
    result = _spawn_result()
    control = SimpleNamespace(
        spawn=AsyncMock(return_value=result),
        emit_status_update=AsyncMock(),
    )

    async def invoke_spawn(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> AgentSpawnOutput:
        del event, event_next
        assert ops is not None
        return await ops.agent.spawn(_request())

    async def outer_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> None:
        del event, ops, event_next
        return None

    bus.register_event_type("test.agent_spawn", lambda _value, pattern: not pattern, outer_core)
    bus.register_event_handler("test.agent_spawn", invoke_spawn, {})

    @with_session(session)
    async def trigger_spawn() -> object:
        return await bus.trigger(Event("test.agent_spawn", None), session)

    try:
        with patch(
            "openjiuwen.harness.tools.subagent._control_registry.get_subagent_control",
            return_value=control,
        ) as get_control:
            output = await trigger_spawn()
    finally:
        get_agent_runtime().unbind(session.get_session_id(), agent)

    assert output == AgentSpawnOutput(
        subagent_id=result.subagent_id,
        status=result.status.kind.value,
    )
    get_control.assert_called_once_with(agent, session)
    control.spawn.assert_awaited_once()
    control.emit_status_update.assert_awaited_once_with(result.subagent_id)
