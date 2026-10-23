# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""EV-04 integration of external build, handler ops and business triggers."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.types import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    HandlerLevel,
    PackageMetadata,
    SessionContext,
)
from openjiuwen.core.single_agent import Session

_BUILD = Event(OPS_BUILD_EVENT_TYPE, None)
_PACKAGE = PackageMetadata("demo.package", "1.0.0")
_SESSION = SessionContext("session-1")


def _matches(event_input: object, pattern: Mapping[str, object]) -> bool:
    return isinstance(event_input, Mapping) and all(event_input.get(key) == value for key, value in pattern.items())


async def _core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
    del event, ops, event_next
    return "core"


async def _echo(ops: EventHandlerOps, value: object) -> object:
    return ops.package_metadata, ops.session_context, value


async def _echo_user(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
    del event_next
    assert ops is not None
    assert isinstance(event.event_input, Mapping)
    return await ops.engine.echo(event.event_input["value"])


def _register_echo_build(bus: ExtensionEventBus, build_calls: list[str]) -> str:
    async def add_engine(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        assert ops is None
        build_calls.append(event.event_type)
        builder = await event_next(event)
        assert isinstance(builder, EventHandlerOpsBuilder)
        builder.add_namespace("engine")
        builder.add_method("engine", "echo", _echo)
        return builder

    return bus.register_event_handler(OPS_BUILD_EVENT_TYPE, add_engine, {}, HandlerLevel.BUILTIN)


@pytest.mark.asyncio
@pytest.mark.parametrize("session", [_SESSION, Session("session-1")], ids=["session_context", "agent_session"])
async def test_external_build_then_handler_uses_ops_capability(session: Session | SessionContext) -> None:
    bus = ExtensionEventBus()
    build_calls: list[str] = []
    _register_echo_build(bus, build_calls)
    bus.register_event_type("demo.echo", _matches, _core)
    bus.register_event_handler("demo.echo", _echo_user, {}, package_metadata=_PACKAGE)

    await bus.trigger(_BUILD)
    result = await bus.trigger(Event("demo.echo", {"value": 7}), session)

    assert result == (_PACKAGE, _SESSION, 7)
    assert build_calls == [OPS_BUILD_EVENT_TYPE]


@pytest.mark.asyncio
async def test_business_trigger_requires_external_build_and_build_commits_once() -> None:
    bus = ExtensionEventBus()
    build_calls: list[str] = []
    build_handler_id = _register_echo_build(bus, build_calls)
    bus.register_event_type("demo.echo", _matches, _core)

    with pytest.raises(ValidationError) as not_built:
        await bus.trigger(Event("demo.echo", {}))
    assert not_built.value.status is StatusCode.EVENTS_STATE_INVALID
    assert build_calls == []

    await bus.trigger(_BUILD)

    with pytest.raises(ValidationError) as rebuild:
        await bus.trigger(_BUILD)
    assert rebuild.value.status is StatusCode.EVENTS_STATE_INVALID
    with pytest.raises(ValidationError) as late_register:
        _register_echo_build(bus, build_calls)
    assert late_register.value.status is StatusCode.EVENTS_STATE_INVALID
    with pytest.raises(ValidationError) as late_unregister:
        bus.unregister_event_handler(build_handler_id)
    assert late_unregister.value.status is StatusCode.EVENTS_STATE_INVALID
    assert build_calls == [OPS_BUILD_EVENT_TYPE]


@pytest.mark.asyncio
async def test_conflicting_build_handler_is_skipped_and_build_commits() -> None:
    bus = ExtensionEventBus()
    build_calls: list[str] = []
    _register_echo_build(bus, build_calls)
    _register_echo_build(bus, build_calls)
    bus.register_event_type("demo.echo", _matches, _core)
    bus.register_event_handler("demo.echo", _echo_user, {}, package_metadata=_PACKAGE)

    # The outer handler hits a duplicate namespace after the inner one ran; it is skipped
    # and the inner handler's Builder is committed without rebuilding.
    assert isinstance(await bus.trigger(_BUILD), EventHandlerOpsBuilder)
    assert build_calls == [OPS_BUILD_EVENT_TYPE, OPS_BUILD_EVENT_TYPE]
    assert await bus.trigger(Event("demo.echo", {"value": 2}), _SESSION) == (_PACKAGE, _SESSION, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["before_next", "after_next", "invalid_output"])
async def test_failed_build_handler_is_skipped_without_rebuilding(failure: str) -> None:
    bus = ExtensionEventBus()
    downstream_calls: list[str] = []

    async def failing_build(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        if failure == "before_next":
            raise RuntimeError("build handler failed")
        builder = await event_next(event)
        if failure == "after_next":
            raise RuntimeError("build handler failed")
        del builder
        return "not a builder"

    async def count_downstream(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        downstream_calls.append(event.event_type)
        return await event_next(event)

    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, failing_build, {}, HandlerLevel.BUILTIN)
    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, count_downstream, {}, HandlerLevel.BUILTIN)

    assert isinstance(await bus.trigger(_BUILD), EventHandlerOpsBuilder)
    assert downstream_calls == [OPS_BUILD_EVENT_TYPE]


@pytest.mark.asyncio
async def test_build_rejects_non_none_input() -> None:
    bus = ExtensionEventBus()

    with pytest.raises(ValidationError) as invalid:
        await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, {}))
    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID
    assert isinstance(await bus.trigger(_BUILD), EventHandlerOpsBuilder)


@pytest.mark.asyncio
async def test_ops_verb_triggers_independent_nested_event_with_same_session() -> None:
    bus = ExtensionEventBus()

    async def run_inner(ops: EventHandlerOps, value: object) -> object:
        return await bus.trigger(Event("demo.inner", {"value": value}), ops.session_context)

    async def add_flow(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        builder = await event_next(event)
        assert isinstance(builder, EventHandlerOpsBuilder)
        builder.add_namespace("flow")
        builder.add_method("flow", "run_inner", run_inner)
        return builder

    async def inner_core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event_next
        assert ops is not None
        assert isinstance(event.event_input, Mapping)
        return "inner", ops.session_context, event.event_input["value"]

    async def outer_user(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        assert ops is not None
        return "outer", await ops.flow.run_inner(3)

    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, add_flow, {}, HandlerLevel.BUILTIN)
    bus.register_event_type("demo.inner", _matches, inner_core)
    bus.register_event_type("demo.outer", _matches, _core)
    bus.register_event_handler("demo.outer", outer_user, {})

    await bus.trigger(_BUILD)

    assert await bus.trigger(Event("demo.outer", {}), _SESSION) == ("outer", ("inner", _SESSION, 3))
