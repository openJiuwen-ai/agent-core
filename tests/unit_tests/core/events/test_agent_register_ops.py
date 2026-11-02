# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for the ``agent.register`` Ops method."""

from __future__ import annotations

from typing import Mapping

import pytest

from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOps,
    EventHandlerOpsBuilder,
    EventNext,
    ExtensionEventBus,
    HandlerLevel,
    SessionContext,
)
from openjiuwen.core.events.builtins.event_types.agent_register import (
    AGENT_REGISTER,
    AgentRegisterInput,
    AgentRegisterOutput,
    AgentRegisterValue,
)
from openjiuwen.core.events.builtins.event_types.agent_register import (
    match_pattern_func as match_agent_register_pattern,
)
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig

_BUILD = Event(OPS_BUILD_EVENT_TYPE, None)


def _matches(event_input: object, pattern: Mapping[str, object]) -> bool:
    del event_input
    return not pattern


async def _build_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    result = await bus.trigger(_BUILD)
    assert isinstance(result, EventHandlerOpsBuilder)
    return bus


def _make_agent(session_id: str) -> DeepAgent:
    agent = DeepAgent(
        AgentCard(id="parent-agent", name="parent-agent", description="parent"),
    ).configure(DeepAgentConfig(enable_task_loop=False))
    get_agent_runtime().bind(session_id, agent)
    return agent


@pytest.mark.asyncio
async def test_agent_register_uses_real_deep_agent_binding() -> None:
    session_id = "registration-agent-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()
    request = AgentRegisterInput(
        agent_card=AgentCard(id="reviewer", name="reviewer", description="review code"),
        prompt="Review the code and explain the evidence.",
    )

    async def trigger_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> AgentRegisterValue:
        del event, event_next
        assert ops is not None
        return await ops.agent.register(request)

    async def outer_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> None:
        del event, ops, event_next
        return None

    bus.register_event_type("test.registration", _matches, outer_core)
    bus.register_event_handler("test.registration", trigger_registration, {})

    try:
        result = await bus.trigger(
            Event("test.registration", None),
            SessionContext(session_id),
        )

        assert isinstance(result, AgentRegisterValue)
        assert result.agent == "reviewer"
        assert agent.deep_config is not None
        assert [item.agent_card.name for item in agent.deep_config.subagents or []] == ["reviewer"]
        registered_card = agent.ability_manager.get("reviewer")
        assert isinstance(registered_card, AgentCard)
        assert registered_card.id == "reviewer"
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_agent_register_short_circuit_does_not_change_real_agent() -> None:
    session_id = "registration-agent-short-circuit-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()
    request = AgentRegisterInput(
        agent_card=AgentCard(id="blocked", name="blocked", description="blocked"),
        prompt="must not be bound",
    )

    async def short_circuit(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> AgentRegisterOutput:
        del event, ops, event_next
        return AgentRegisterOutput(
            value=AgentRegisterValue(request.agent_card.name, request.agent_card),
        )

    async def trigger_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> AgentRegisterValue:
        del event, event_next
        assert ops is not None
        return await ops.agent.register(request)

    async def outer_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> None:
        del event, ops, event_next
        return None

    bus.register_event_type("test.registration", _matches, outer_core)
    bus.register_event_handler(AGENT_REGISTER, short_circuit, {}, HandlerLevel.USER)
    bus.register_event_handler("test.registration", trigger_registration, {})

    try:
        result = await bus.trigger(
            Event("test.registration", None),
            SessionContext(session_id),
        )

        assert isinstance(result, AgentRegisterValue)
        assert agent.deep_config is not None
        assert agent.deep_config.subagents in (None, [])
        assert agent.ability_manager.get("blocked") is None
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_agent_register_core_requires_the_bound_runtime_agent() -> None:
    bus = await _build_bus()
    request = AgentRegisterInput(
        agent_card=AgentCard(id="missing", name="missing", description="missing"),
        prompt="must not run",
    )

    with pytest.raises(RuntimeError, match="not bound"):
        await bus.trigger(
            Event("agent.register", request),
            SessionContext("unbound-agent-session"),
        )


@pytest.mark.asyncio
async def test_agent_register_rejects_unsupported_fields_without_side_effect() -> None:
    session_id = "registration-agent-unsupported-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()
    request = AgentRegisterInput(
        agent_card=AgentCard(id="unsupported", name="unsupported", description="unsupported"),
        prompt="unsupported",
        tools=("shell",),
    )

    async def trigger_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> AgentRegisterValue:
        del event, event_next
        assert ops is not None
        return await ops.agent.register(request)

    async def outer_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> None:
        del event, ops, event_next
        return None

    bus.register_event_type("test.registration", _matches, outer_core)
    bus.register_event_handler("test.registration", trigger_registration, {})

    try:
        result = await bus.trigger(
            Event("test.registration", None),
            SessionContext(session_id),
        )
        assert result is None
        assert agent.deep_config is not None
        assert agent.deep_config.subagents in (None, [])
        assert agent.ability_manager.get("unsupported") is None
    finally:
        get_agent_runtime().unbind(session_id, agent)


def test_agent_register_matcher_ignores_nonempty_patterns_without_raising() -> None:
    request = AgentRegisterInput(
        agent_card=AgentCard(id="matcher-agent", name="matcher-agent", description="matcher"),
        prompt="matcher",
    )

    assert match_agent_register_pattern(request, {"scope": "user"}) is False


def test_agent_register_output_rejects_invalid_nested_values() -> None:
    with pytest.raises(TypeError, match="value must be AgentRegisterValue"):
        AgentRegisterOutput(value="invalid")  # type: ignore[arg-type]
