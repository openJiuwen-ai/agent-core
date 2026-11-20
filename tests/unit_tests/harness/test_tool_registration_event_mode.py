# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Event-mode tool registration at DeepAgent construction boundaries."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOpsBuilder,
    ExtensionEventBus,
)
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import (
    apply_deep_agent_parts,
    create_deep_agent,
    resolve_deep_agent_parts,
)
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail


@dataclass
class _RecordingBus:
    calls: list[str]
    failures_remaining: int = 0

    async def trigger(self, event: Event, session=None, *, trigger_context=None):
        del session
        assert event.event_type == "tool.register"
        assert trigger_context is not None
        self.calls.append(event.event_input.source.card.name)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("registration failed")
        return object()


def _tool(name: str) -> LocalFunction:
    return LocalFunction(
        card=ToolCard(id=name, name=name, description=f"Tool {name}."),
        func=lambda: name,
    )


def _event_agent(bus: _RecordingBus, tools: list[LocalFunction]):
    return create_deep_agent(
        model=None,
        card=AgentCard(name="event-mode-agent", description="test"),
        tools=tools,
        extension_bus=bus,
        tool_registration_mode="event",
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_tool_resilience_rail=False,
        rails=[],
    )


@pytest.mark.asyncio
async def test_event_mode_flushes_factory_tools_in_fifo_order_and_is_idempotent() -> None:
    bus = _RecordingBus(calls=[])
    agent = _event_agent(bus, [_tool("first"), _tool("second")])

    await agent.ensure_initialized()
    await agent.ensure_initialized()

    assert bus.calls == ["first", "second"]
    assert agent._pending_tool_registrations == []


@pytest.mark.asyncio
async def test_event_mode_factory_tool_uses_core_handler_without_session() -> None:
    bus = ExtensionEventBus()
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(builder, EventHandlerOpsBuilder)
    tool = _tool("core_event_tool")

    await Runner.start()
    agent = _event_agent(bus, [tool])
    try:
        await agent.ensure_initialized()

        assert agent.ability_manager.get("core_event_tool") is tool.card
        assert Runner.resource_mgr.get_tool(tool.card.id) is tool
    finally:
        agent.ability_manager.remove_ability("core_event_tool")
        await Runner.stop()


@pytest.mark.asyncio
async def test_event_mode_can_leave_non_event_factory_tools_on_legacy_path() -> None:
    bus = _RecordingBus(calls=[])
    event_tool = _tool("event_tool")
    legacy_tool = _tool("legacy_tool")
    agent = create_deep_agent(
        model=None,
        card=AgentCard(name="mixed-mode-agent", description="test"),
        tools=[event_tool, legacy_tool],
        event_tool_instances=[event_tool],
        extension_bus=bus,
        tool_registration_mode="event",
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_tool_resilience_rail=False,
        rails=[],
    )

    await agent.ensure_initialized()

    assert bus.calls == ["event_tool"]
    assert agent.ability_manager.get("legacy_tool") is legacy_tool.card
    assert agent._pending_tool_registrations == []


@pytest.mark.asyncio
async def test_event_mode_retains_uncompleted_tools_after_flush_failure() -> None:
    bus = _RecordingBus(calls=[], failures_remaining=1)
    agent = _event_agent(bus, [_tool("first"), _tool("second")])

    with pytest.raises(RuntimeError, match="registration failed"):
        await agent.ensure_initialized()

    assert [tool.card.name for tool in agent._pending_tool_registrations] == [
        "first",
        "second",
    ]

    await agent.ensure_initialized()

    assert bus.calls == ["first", "first", "second"]
    assert agent._pending_tool_registrations == []


@pytest.mark.asyncio
async def test_event_mode_sys_operation_rail_queues_tools_until_rail_flush() -> None:
    bus = _RecordingBus(calls=[])
    agent = create_deep_agent(
        model=None,
        card=AgentCard(name="event-mode-sysop-agent", description="test"),
        workspace=".",
        extension_bus=bus,
        tool_registration_mode="event",
        rails=[SysOperationRail()],
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_tool_resilience_rail=False,
        auto_create_workspace=False,
    )

    await agent.ensure_initialized()

    assert "read_file" in bus.calls
    assert "write_file" in bus.calls
    assert agent._pending_tool_registrations == []
    assert agent.ability_manager.get("read_file") is None


@pytest.mark.asyncio
async def test_event_mode_rail_retry_does_not_requeue_completed_initialization() -> None:
    bus = _RecordingBus(calls=[], failures_remaining=1)
    agent = create_deep_agent(
        model=None,
        card=AgentCard(name="event-mode-retry-agent", description="test"),
        workspace=".",
        extension_bus=bus,
        tool_registration_mode="event",
        rails=[SysOperationRail()],
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_tool_resilience_rail=False,
        auto_create_workspace=False,
    )

    with pytest.raises(RuntimeError, match="registration failed"):
        await agent.ensure_initialized()

    await agent.ensure_initialized()

    assert bus.calls.count(bus.calls[0]) == 2
    assert len(bus.calls) == len(set(bus.calls)) + 1


@pytest.mark.asyncio
async def test_event_mode_hot_reconfigure_keeps_concrete_tools_on_event_path() -> None:
    bus = _RecordingBus(calls=[])
    agent = _event_agent(bus, [_tool("initial_tool")])
    await agent.ensure_initialized()

    reconfigured_tool = _tool("reconfigured_tool")
    parts = resolve_deep_agent_parts(
        model=None,
        card=agent.card,
        tools=[reconfigured_tool],
        extension_bus=bus,
        tool_registration_mode="event",
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_tool_resilience_rail=False,
        rails=[],
    )

    apply_deep_agent_parts(agent, parts)

    assert agent.ability_manager.get("reconfigured_tool") is None
    await agent.ensure_initialized()

    assert bus.calls == ["initial_tool", "reconfigured_tool"]


@pytest.mark.asyncio
async def test_event_mode_hot_reconfigure_preserves_legacy_tool_cards() -> None:
    bus = _RecordingBus(calls=[])
    agent = _event_agent(bus, [_tool("initial_tool")])
    await agent.ensure_initialized()

    event_tool = _tool("reconfigured_event_tool")
    legacy_card = ToolCard(
        id="reconfigured_legacy_card",
        name="reconfigured_legacy_card",
        description="Legacy card.",
    )
    parts = resolve_deep_agent_parts(
        model=None,
        card=agent.card,
        tools=[event_tool, legacy_card],
        event_tool_instances=[event_tool],
        extension_bus=bus,
        tool_registration_mode="event",
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
        enable_tool_resilience_rail=False,
        rails=[],
    )

    apply_deep_agent_parts(agent, parts)

    assert agent.ability_manager.get("reconfigured_event_tool") is None
    assert agent.ability_manager.get("reconfigured_legacy_card") is legacy_card
    await agent.ensure_initialized()

    assert bus.calls == ["initial_tool", "reconfigured_event_tool"]
