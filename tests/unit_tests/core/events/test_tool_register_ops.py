# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for the ``tool.register`` Ops method."""

from __future__ import annotations

from typing import Mapping

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
    HandlerLevel,
    SessionContext,
)
from openjiuwen.core.events.builtins.event_types.tool_register import (
    TOOL_REGISTER,
    ToolRegisterInput,
    ToolRegisterOutput,
    ToolRegisterSpec,
    ToolRegisterValue,
)
from openjiuwen.core.events.builtins.event_types.tool_register import (
    match_pattern_func as match_tool_register_pattern,
)
from openjiuwen.core.foundation.tool import LocalFunction, RestfulApi, RestfulApiCard, ToolCard
from openjiuwen.core.runner import Runner
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
async def test_tool_register_binds_and_executes_a_real_local_function() -> None:
    session_id = "registration-tool-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()

    async def echo(value: str) -> str:
        return f"echo:{value}"

    spec = ToolRegisterSpec(
        source=echo,
        card=ToolCard(
            id="echo",
            name="echo",
            description="Echo one value.",
            input_params={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ),
    )

    async def trigger_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> ToolRegisterValue:
        del event, event_next
        assert ops is not None
        return await ops.tool.register(spec)

    async def outer_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> None:
        del event, ops, event_next
        return None

    received_inputs: list[ToolRegisterInput] = []

    async def capture_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        received_inputs.append(event.event_input)
        return await event_next(event)

    bus.register_event_type("test.registration", _matches, outer_core)
    bus.register_event_handler(TOOL_REGISTER, capture_registration, {}, HandlerLevel.USER)
    bus.register_event_handler("test.registration", trigger_registration, {})

    await Runner.start()
    try:
        result = await bus.trigger(
            Event("test.registration", None),
            SessionContext(session_id),
        )

        assert isinstance(result, ToolRegisterValue)
        assert result.tool == "echo"
        assert len(received_inputs) == 1
        assert received_inputs[0].source is echo
        assert received_inputs[0].card is not None
        assert received_inputs[0].card.name == "echo"
        registered_card = agent.ability_manager.get("echo")
        assert isinstance(registered_card, ToolCard)
        registered_tool = Runner.resource_mgr.get_tool(registered_card.id)
        assert registered_tool is not None
        assert await registered_tool.invoke({"value": "hello"}) == "echo:hello"
    finally:
        agent.ability_manager.remove_ability("echo")
        get_agent_runtime().unbind(session_id, agent)
        await Runner.stop()


@pytest.mark.asyncio
async def test_tool_register_accepts_existing_tool_with_default_schema() -> None:
    session_id = "registration-existing-tool-session"
    agent = _make_agent(session_id)
    bus = ExtensionEventBus()
    builder = await bus.trigger(_BUILD)
    assert isinstance(builder, EventHandlerOpsBuilder)
    ops = builder.build(session_context=SessionContext(session_id))

    async def echo() -> str:
        return "existing"

    tool = LocalFunction(
        card=ToolCard(
            id="existing_tool",
            name="existing_tool",
            description="Existing tool.",
        ),
        func=echo,
    )

    await Runner.start()
    try:
        result = await ops.tool.register(
            ToolRegisterSpec(source=tool)
        )

        assert result.tool == "existing_tool"
        registered_card = agent.ability_manager.get("existing_tool")
        assert registered_card is tool.card
        registered_tool = Runner.resource_mgr.get_tool(registered_card.id)
        assert registered_tool is tool
        assert await registered_tool.invoke({}) == "existing"
    finally:
        agent.ability_manager.remove_ability("existing_tool")
        get_agent_runtime().unbind(session_id, agent)
        await Runner.stop()


@pytest.mark.asyncio
async def test_tool_register_materializes_restful_api_card() -> None:
    session_id = "registration-restful-card-session"
    agent = _make_agent(session_id)
    bus = ExtensionEventBus()
    card = RestfulApiCard(
        id="weather_api",
        name="weather_api",
        description="Query weather from an HTTP API.",
        url="https://example.com/weather",
        method="GET",
        input_params={"type": "object", "properties": {}},
    )
    received_inputs: list[ToolRegisterInput] = []

    async def capture_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        received_inputs.append(event.event_input)
        return await event_next(event)

    bus.register_event_handler(TOOL_REGISTER, capture_registration, {}, HandlerLevel.USER)

    await Runner.start()
    try:
        builder = await bus.trigger(_BUILD)
        assert isinstance(builder, EventHandlerOpsBuilder)
        ops = builder.build(session_context=SessionContext(session_id))

        result = await ops.tool.register(ToolRegisterSpec(source=card))

        assert result.tool == "weather_api"
        assert len(received_inputs) == 1
        assert received_inputs[0].source is card
        registered_card = agent.ability_manager.get("weather_api")
        assert registered_card is card
        registered_tool = Runner.resource_mgr.get_tool(registered_card.id)
        assert isinstance(registered_tool, RestfulApi)
        assert registered_tool.card is card
    finally:
        agent.ability_manager.remove_ability("weather_api")
        get_agent_runtime().unbind(session_id, agent)
        await Runner.stop()


@pytest.mark.asyncio
async def test_tool_register_reuses_existing_restful_api() -> None:
    session_id = "registration-existing-restful-session"
    agent = _make_agent(session_id)
    bus = ExtensionEventBus()
    tool = RestfulApi(
        card=RestfulApiCard(
            id="existing_weather_api",
            name="existing_weather_api",
            description="Existing weather HTTP API.",
            url="https://example.com/weather",
            method="GET",
            input_params={"type": "object", "properties": {}},
        )
    )

    await Runner.start()
    try:
        builder = await bus.trigger(_BUILD)
        assert isinstance(builder, EventHandlerOpsBuilder)
        ops = builder.build(session_context=SessionContext(session_id))

        result = await ops.tool.register(ToolRegisterSpec(source=tool))

        assert result.tool == "existing_weather_api"
        registered_card = agent.ability_manager.get("existing_weather_api")
        assert registered_card is tool.card
        assert Runner.resource_mgr.get_tool(registered_card.id) is tool
    finally:
        agent.ability_manager.remove_ability("existing_weather_api")
        get_agent_runtime().unbind(session_id, agent)
        await Runner.stop()


@pytest.mark.asyncio
async def test_tool_register_event_requires_the_bound_runtime_agent() -> None:
    bus = await _build_bus()
    request = ToolRegisterInput(
        source=LocalFunction(
            card=ToolCard(id="direct_event", name="direct_event", description="Direct event."),
            func=lambda: "direct",
        ),
    )

    with pytest.raises(RuntimeError, match="not bound"):
        await bus.trigger(Event(TOOL_REGISTER, request), SessionContext("direct-event-session"))


@pytest.mark.asyncio
async def test_tool_register_event_rejects_callable_nested_in_schema() -> None:
    bus = await _build_bus()
    request = ToolRegisterInput(
        source=lambda: "callable schema",
        card=ToolCard(
            id="callable_schema",
            name="callable_schema",
            description="Callable schema.",
            input_params={"type": "object", "x-runtime": lambda: None},
        ),
    )

    with pytest.raises(ValidationError) as invalid:
        await bus.trigger(Event(TOOL_REGISTER, request), SessionContext("callable-schema-session"))

    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID


@pytest.mark.asyncio
async def test_tool_register_short_circuit_skips_core() -> None:
    session_id = "registration-tool-short-circuit-session"
    agent = _make_agent(session_id)
    bus = await _build_bus()

    def blocked_tool() -> str:
        return "blocked"

    spec = ToolRegisterSpec(source=blocked_tool)

    async def short_circuit(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> ToolRegisterOutput:
        del event, ops, event_next
        return ToolRegisterOutput(value=ToolRegisterValue("blocked_tool"))

    async def trigger_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> ToolRegisterValue:
        del event, event_next
        assert ops is not None
        return await ops.tool.register(spec)

    async def outer_core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> None:
        del event, ops, event_next
        return None

    bus.register_event_type("test.registration", _matches, outer_core)
    bus.register_event_handler(TOOL_REGISTER, short_circuit, {}, HandlerLevel.USER)
    bus.register_event_handler("test.registration", trigger_registration, {})

    try:
        result = await bus.trigger(
            Event("test.registration", None),
            SessionContext(session_id),
        )

        assert isinstance(result, ToolRegisterValue)
        assert result.tool == "blocked_tool"
        assert agent.ability_manager.get("blocked_tool") is None
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_tool_register_requires_a_real_implementation() -> None:
    session_id = "registration-tool-missing-implementation-session"
    agent = _make_agent(session_id)
    bus = ExtensionEventBus()
    builder = await bus.trigger(_BUILD)
    assert isinstance(builder, EventHandlerOpsBuilder)
    ops = builder.build(session_context=SessionContext(session_id))

    try:
        with pytest.raises(TypeError, match="source must be a Tool, RestfulApiCard, or callable"):
            await ops.tool.register(
                ToolRegisterSpec(
                    source=ToolCard(
                        id="missing_impl",
                        name="missing_impl",
                        description="Missing implementation.",
                    ),
                )
            )
        assert agent.ability_manager.get("missing_impl") is None
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_tool_register_rejects_non_tool_card_metadata() -> None:
    bus = ExtensionEventBus()
    builder = await bus.trigger(_BUILD)
    assert isinstance(builder, EventHandlerOpsBuilder)
    ops = builder.build(session_context=SessionContext("invalid-card-type-session"))

    def local_function() -> str:
        return "local"

    with pytest.raises(TypeError, match="card must be ToolCard"):
        await ops.tool.register(  # type: ignore[arg-type]
            ToolRegisterSpec(source=local_function, card=object())  # type: ignore[arg-type]
        )


def test_tool_register_matcher_ignores_nonempty_patterns_without_raising() -> None:
    tool_input = ToolRegisterInput(
        source=LocalFunction(
            card=ToolCard(id="matcher_tool", name="matcher_tool", description="Matcher tool."),
            func=lambda: "ok",
        ),
    )

    assert match_tool_register_pattern(tool_input, {"scope": "user"}) is False


def test_tool_register_output_rejects_invalid_nested_values() -> None:
    with pytest.raises(TypeError, match="value must be ToolRegisterValue"):
        ToolRegisterOutput(value=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_tool_register_normalizes_default_schema_in_event() -> None:
    session_id = "registration-default-schema-session"
    agent = _make_agent(session_id)
    bus = ExtensionEventBus()

    def default_schema() -> str:
        return "default"

    spec = ToolRegisterSpec(source=default_schema)

    received_inputs: list[ToolRegisterInput] = []

    async def capture_registration(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        received_inputs.append(event.event_input)
        return await event_next(event)

    bus.register_event_handler(TOOL_REGISTER, capture_registration, {}, HandlerLevel.USER)

    try:
        builder = await bus.trigger(_BUILD)
        assert isinstance(builder, EventHandlerOpsBuilder)
        ops = builder.build(session_context=SessionContext(session_id))

        result = await ops.tool.register(spec)

        assert result.tool == "default_schema"
        assert len(received_inputs) == 1
        assert received_inputs[0].source is default_schema
        registered_card = agent.ability_manager.get("default_schema")
        assert registered_card is not None
        registered_tool = Runner.resource_mgr.get_tool(registered_card.id)
        assert registered_tool.card.input_params["type"] == "object"
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_tool_register_rejects_invalid_schema_before_core() -> None:
    session_id = "registration-invalid-schema-session"
    agent = _make_agent(session_id)
    bus = ExtensionEventBus()

    def invalid_schema() -> str:
        return "invalid"

    spec = ToolRegisterSpec(
        source=invalid_schema,
        card=ToolCard(
            id="invalid_schema",
            name="invalid_schema",
            description="Invalid schema.",
            input_params={"type": "string"},
        ),
    )
    try:
        builder = await bus.trigger(_BUILD)
        assert isinstance(builder, EventHandlerOpsBuilder)
        ops = builder.build(session_context=SessionContext(session_id))

        with pytest.raises(ValueError, match="object"):
            await ops.tool.register(spec)
    finally:
        get_agent_runtime().unbind(session_id, agent)
