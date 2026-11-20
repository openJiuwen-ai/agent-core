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
    AgentRegisterTriggerContext,
    AgentRegisterValue,
    prepare_agent_register_input,
)
from openjiuwen.core.events.builtins.event_types.agent_register import (
    match_pattern_func as match_agent_register_pattern,
)
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig, SubAgentConfig

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


@pytest.mark.asyncio
async def test_agent_register_accepts_subagent_config_as_canonical_payload() -> None:
    session_id = "registration-agent-config-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()
    card = AgentCard(id="configured", name="configured", description="configured agent")
    tool_card = ToolCard(id="configured-tool", name="configured-tool", description="configured tool")
    config = SubAgentConfig(
        agent_card=card,
        system_prompt="Use the configured capabilities.",
        tools=[tool_card],
        skills=["review"],
        max_iterations=7,
        factory_name="review_agent",
        factory_kwargs={"policy": {"mode": "strict"}},
        role="reviewer",
    )
    envelope = AgentRegisterInput.from_subagent_config(config)
    assert envelope.agent_card is card
    assert envelope.prompt == config.system_prompt
    assert envelope.subagent_config is config

    async def trigger_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> AgentRegisterValue:
        del event, event_next
        assert ops is not None
        return await ops.agent.register(config)

    async def outer_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> None:
        del event, ops, event_next
        return None

    received: list[AgentRegisterInput] = []

    async def capture_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        received.append(event.event_input)
        return await event_next(event)

    bus.register_event_type("test.registration", _matches, outer_core)
    bus.register_event_handler(AGENT_REGISTER, capture_registration, {}, HandlerLevel.USER)
    bus.register_event_handler("test.registration", trigger_registration, {})

    try:
        result = await bus.trigger(
            Event("test.registration", None),
            SessionContext(session_id),
        )

        assert isinstance(result, AgentRegisterValue)
        assert len(received) == 1
        prepared = received[0]
        assert prepared.subagent_config is not config
        assert prepared.agent_card is not card
        assert prepared.agent_card.name == "configured"
        assert prepared.prompt == "Use the configured capabilities."
        assert agent.deep_config is not None
        registered = agent.deep_config.subagents or []
        assert len(registered) == 1
        assert registered[0].agent_card.name == "configured"
        assert registered[0].tools == [tool_card]
        assert registered[0].skills == ["review"]
        assert registered[0].max_iterations == 7
        assert registered[0].factory_name == "review_agent"
        assert registered[0].factory_kwargs == {"policy": {"mode": "strict"}}
        assert registered[0].role == "reviewer"
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_agent_register_snapshots_config_mutable_containers() -> None:
    session_id = "registration-agent-config-snapshot-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()
    config = SubAgentConfig(
        agent_card=AgentCard(id="snapshot", name="snapshot", description="snapshot"),
        system_prompt="snapshot prompt",
        skills=["first"],
        factory_kwargs={"options": {"mode": "strict"}},
    )

    async def trigger_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> AgentRegisterValue:
        del event, event_next
        assert ops is not None
        return await ops.agent.register(AgentRegisterInput.from_subagent_config(config))

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
        await bus.trigger(Event("test.registration", None), SessionContext(session_id))
        config.skills.append("mutated")
        config.factory_kwargs["options"]["mode"] = "mutated"
        config.agent_card.description = "mutated"

        assert agent.deep_config is not None
        registered = (agent.deep_config.subagents or [])[0]
        assert registered.skills == ["first"]
        assert registered.factory_kwargs == {"options": {"mode": "strict"}}
        assert registered.agent_card.description == "snapshot"
    finally:
        get_agent_runtime().unbind(session_id, agent)


def test_agent_register_snapshot_accepts_optional_tool_and_mcp_collections() -> None:
    config = SubAgentConfig(
        agent_card=AgentCard(id="optional-collections", name="optional-collections", description="optional"),
        system_prompt="optional collections prompt",
        tools=None,  # type: ignore[arg-type]
        mcps=None,  # type: ignore[arg-type]
    )

    prepared = prepare_agent_register_input(
        AgentRegisterInput.from_subagent_config(config),
    )

    assert prepared.subagent_config is not config
    assert prepared.subagent_config is not None
    assert prepared.subagent_config.tools is None
    assert prepared.subagent_config.mcps is None


def test_agent_register_matcher_ignores_nonempty_patterns_without_raising() -> None:
    request = AgentRegisterInput(
        agent_card=AgentCard(id="matcher-agent", name="matcher-agent", description="matcher"),
        prompt="matcher",
    )

    assert match_agent_register_pattern(request, {"scope": "user"}) is False


def test_agent_register_output_rejects_invalid_nested_values() -> None:
    with pytest.raises(TypeError, match="value must be AgentRegisterValue"):
        AgentRegisterOutput(value="invalid")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_agent_register_startup_trigger_context_binds_without_session() -> None:
    session_id = "registration-agent-trigger-context-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()
    request = AgentRegisterInput(
        subagent_config=SubAgentConfig(
            agent_card=AgentCard(id="startup", name="startup", description="startup"),
            system_prompt="startup prompt",
        )
    )

    try:
        result = await bus.trigger(
            Event(AGENT_REGISTER, request),
            trigger_context=AgentRegisterTriggerContext(agent=agent),
        )

        assert isinstance(result, AgentRegisterOutput)
        assert result.value is not None
        assert agent.ability_manager.get("startup") is not None
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_deep_agent_flushes_queued_subagents_through_register_event() -> None:
    bus = await _build_bus()
    config = SubAgentConfig(
        agent_card=AgentCard(id="queued", name="queued", description="queued"),
        system_prompt="queued prompt",
        skills=["first"],
        factory_kwargs={"options": {"mode": "strict"}},
    )
    agent = DeepAgent(
        AgentCard(id="parent-queued", name="parent-queued", description="parent"),
        extension_bus=bus,
    ).configure(
        DeepAgentConfig(
            enable_task_loop=False,
            subagent_registration_mode="event",
            event_subagent_configs=[config],
        )
    )

    await agent._flush_subagent_registrations()  # pylint: disable=protected-access
    config.skills.append("mutated")
    config.factory_kwargs["options"]["mode"] = "mutated"
    config.agent_card.description = "mutated"

    assert agent.deep_config is not None
    registered = (agent.deep_config.subagents or [])[0]
    assert registered.agent_card.name == "queued"
    assert registered.agent_card.description == "queued"
    assert registered.skills == ["first"]
    assert registered.factory_kwargs == {"options": {"mode": "strict"}}


@pytest.mark.asyncio
async def test_deep_agent_event_mode_partitions_direct_config_subagents() -> None:
    bus = await _build_bus()
    config = SubAgentConfig(
        agent_card=AgentCard(id="direct-config", name="direct-config", description="direct"),
        system_prompt="direct config prompt",
    )
    direct_config = DeepAgentConfig(
        enable_task_loop=False,
        subagent_registration_mode="event",
        subagents=[config],
    )
    agent = DeepAgent(
        AgentCard(id="parent-direct-config", name="parent-direct-config", description="parent"),
        extension_bus=bus,
    ).configure(direct_config)

    assert agent.deep_config is not None
    assert agent.deep_config.subagents is None
    assert agent.deep_config.event_subagent_configs == [config]
    assert direct_config.subagents == [config]

    await agent._flush_subagent_registrations()  # pylint: disable=protected-access

    assert [item.agent_card.name for item in agent.deep_config.subagents or []] == [
        "direct-config"
    ]


def test_event_subagent_hot_reconfigure_preserves_existing_and_queues_new() -> None:
    old_config = SubAgentConfig(
        agent_card=AgentCard(id="old", name="old", description="old"),
        system_prompt="old prompt",
    )
    new_config = SubAgentConfig(
        agent_card=AgentCard(id="new", name="new", description="new"),
        system_prompt="new prompt",
    )
    agent = DeepAgent(AgentCard(id="parent-hot", name="parent-hot", description="parent"))
    previous = DeepAgentConfig(
        subagent_registration_mode="event",
        event_subagent_configs=[old_config],
        subagents=[old_config],
    )
    updated = DeepAgentConfig(
        subagent_registration_mode="event",
        event_subagent_configs=[old_config, new_config],
        subagents=[],
    )
    agent._deep_config = previous  # pylint: disable=protected-access

    updated = agent._prepare_event_subagent_hot_reconfigure(  # pylint: disable=protected-access
        updated,
        previous,
    )

    assert updated.subagents == [old_config]
    assert agent._pending_subagent_registrations == [new_config]  # pylint: disable=protected-access
