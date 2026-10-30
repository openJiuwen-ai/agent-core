# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Registration behavior for the internal EV-02 event bus."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.bus import ExtensionEventBus, _wrap_registry_key
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel


def _matches(event_input: object, pattern: Mapping[str, object]) -> bool:
    return isinstance(event_input, Mapping) and all(event_input.get(key) == value for key, value in pattern.items())


async def _async_matcher(event_input: object, pattern: Mapping[str, object]) -> bool:
    del event_input, pattern
    return True


def _input_is_mapping(event_input: object) -> bool:
    return isinstance(event_input, Mapping)


def _output_is_mapping(event_output: object | None) -> bool:
    return isinstance(event_output, Mapping)


async def _async_validator(value: object) -> bool:
    del value
    return True


async def _core_handler(event: Event, ops: object | None, event_next: EventNext) -> object:
    del ops, event_next
    return event.event_input


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "matcher", "core_handler"),
    [
        ("", _matches, _core_handler),
        ("demo.invalid_matcher", object(), _core_handler),
        ("demo.async_matcher", _async_matcher, _core_handler),
        ("demo.invalid_core", _matches, object()),
    ],
)
async def test_event_type_registration_is_atomic_and_bus_scoped(
    event_type: str,
    matcher: object,
    core_handler: object,
) -> None:
    first_bus = ExtensionEventBus()
    second_bus = ExtensionEventBus()
    first_bus.register_event_type("demo.shared", _matches, _core_handler)
    second_bus.register_event_type("demo.shared", _matches, _core_handler)

    first_dispatch = first_bus._events["demo.shared"].dispatch
    second_dispatch = second_bus._events["demo.shared"].dispatch
    assert await first_dispatch(
        Event("demo.shared", {"bus": "first"}),
        matched_handler_ids=frozenset(),
        target_level=None,
        session_context=None,
    ) == {"bus": "first"}
    assert await second_dispatch(
        Event("demo.shared", {"bus": "second"}),
        matched_handler_ids=frozenset(),
        target_level=None,
        session_context=None,
    ) == {"bus": "second"}

    with pytest.raises(ValidationError) as duplicate:
        first_bus.register_event_type("demo.shared", _matches, _core_handler)
    assert duplicate.value.status is StatusCode.EVENTS_REGISTRATION_INVALID

    before_events = dict(first_bus._events)
    with pytest.raises(ValidationError) as invalid:
        first_bus.register_event_type(event_type, matcher, core_handler)
    assert invalid.value.status is StatusCode.EVENTS_REGISTRATION_INVALID
    assert first_bus._events == before_events


def test_event_type_registration_stores_sync_validators() -> None:
    bus = ExtensionEventBus()

    bus.register_event_type(
        "demo.validated",
        _matches,
        _core_handler,
        input_validator=_input_is_mapping,
        output_validator=_output_is_mapping,
    )

    registration = bus._events["demo.validated"]
    assert registration.input_validator is _input_is_mapping
    assert registration.output_validator is _output_is_mapping


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"input_validator": object()}, "input validator"),
        ({"output_validator": object()}, "output validator"),
        ({"input_validator": _async_validator}, "input validator"),
        ({"output_validator": _async_validator}, "output validator"),
    ],
)
def test_event_type_registration_rejects_invalid_validators_without_state(
    options: dict[str, object],
    reason: str,
) -> None:
    bus = ExtensionEventBus()
    before_events = dict(bus._events)

    with pytest.raises(ValidationError, match=reason) as invalid:
        bus.register_event_type("demo.invalid_validator", _matches, _core_handler, **options)

    assert invalid.value.status is StatusCode.EVENTS_REGISTRATION_INVALID
    assert bus._events == before_events


def test_new_bus_preregisters_ops_build() -> None:
    bus = ExtensionEventBus()
    handler_id = bus.register_event_handler(OPS_BUILD_EVENT_TYPE, _core_handler, {}, HandlerLevel.BUILTIN)

    with pytest.raises(ValidationError) as overwrite:
        bus.register_event_type(OPS_BUILD_EVENT_TYPE, _matches, _core_handler)
    assert overwrite.value.status is StatusCode.EVENTS_REGISTRATION_INVALID

    bus.unregister_event_type(OPS_BUILD_EVENT_TYPE)
    assert OPS_BUILD_EVENT_TYPE not in bus._events
    assert handler_id not in bus._handlers


@pytest.mark.asyncio
async def test_core_handler_result_must_be_awaitable() -> None:
    bus = ExtensionEventBus()

    def non_awaitable_core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "not-awaitable"

    bus.register_event_type("demo.non_awaitable_core", _matches, non_awaitable_core)
    dispatch = bus._events["demo.non_awaitable_core"].dispatch

    with pytest.raises(ValidationError) as invalid:
        await dispatch(
            Event("demo.non_awaitable_core", {}),
            matched_handler_ids=frozenset(),
            target_level=None,
            session_context=None,
        )
    assert invalid.value.status is StatusCode.EVENTS_REGISTRATION_INVALID


@pytest.mark.asyncio
async def test_handler_registration_freezes_pattern_and_unregisters_exact_record() -> None:
    bus = ExtensionEventBus()
    initial_handlers = dict(bus._handlers)
    pattern: dict[str, object] = {"kind": "match", "nested": {"version": 1}}

    async def handler(current: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        return await event_next(current)

    bus.register_event_type("demo.handlers", _matches, _core_handler)
    first_id = bus.register_event_handler("demo.handlers", handler, pattern, HandlerLevel.USER)
    second_id = bus.register_event_handler("demo.handlers", handler, pattern, HandlerLevel.USER)
    builtin_id = bus.register_event_handler("demo.handlers", handler, pattern, HandlerLevel.BUILTIN)

    assert first_id != second_id
    assert len({first_id, second_id, builtin_id}) == 3
    pattern["kind"] = "changed"
    nested = pattern["nested"]
    assert isinstance(nested, dict)
    nested["version"] = 2
    assert bus._handlers[first_id].match_pattern == {"kind": "match", "nested": {"version": 1}}
    registry_key = _wrap_registry_key("demo.handlers")
    assert [callback["priority"] for callback in bus._framework.list_callbacks(registry_key)] == [200, 200, 100]

    bus.unregister_event_handler(first_id)
    bus.unregister_event_handler("unknown-handler")

    assert first_id not in bus._handlers
    assert set(bus._handlers) == set(initial_handlers) | {second_id, builtin_id}
    assert {hid: bus._handlers[hid] for hid in initial_handlers} == initial_handlers
    assert [callback["priority"] for callback in bus._framework.list_callbacks(registry_key)] == [200, 100]


@pytest.mark.parametrize(
    ("event_type", "handler", "pattern", "level", "options", "expected_status"),
    [
        ("demo.unknown", _core_handler, {}, HandlerLevel.USER, {}, StatusCode.EVENTS_REGISTRATION_INVALID),
        ("demo.handlers", object(), {}, HandlerLevel.USER, {}, StatusCode.EVENTS_REGISTRATION_INVALID),
        (
            "demo.handlers",
            _core_handler,
            {"invalid": object()},
            HandlerLevel.USER,
            {},
            StatusCode.EVENTS_REGISTRATION_INVALID,
        ),
        ("demo.handlers", _core_handler, {}, HandlerLevel.CORE, {}, StatusCode.EVENTS_REGISTRATION_INVALID),
        ("ops.custom", _core_handler, {}, HandlerLevel.USER, {}, StatusCode.EVENTS_REGISTRATION_INVALID),
        (
            "demo.handlers",
            _core_handler,
            {},
            HandlerLevel.USER,
            {"package_metadata": "demo.package"},
            StatusCode.EVENTS_PAYLOAD_INVALID,
        ),
    ],
)
def test_handler_registration_rejects_invalid_input_without_state(
    event_type: str,
    handler: object,
    pattern: object,
    level: HandlerLevel,
    options: dict[str, object],
    expected_status: StatusCode,
) -> None:
    bus = ExtensionEventBus()
    bus.register_event_type("demo.handlers", _matches, _core_handler)
    initial_handlers = dict(bus._handlers)

    with pytest.raises(ValidationError) as invalid:
        bus.register_event_handler(event_type, handler, pattern, level, **options)
    assert invalid.value.status is expected_status
    assert bus._handlers == initial_handlers
    assert bus._framework.list_callbacks(_wrap_registry_key("demo.handlers")) == []


@pytest.mark.asyncio
async def test_event_type_unregistration_cascades_handlers_and_allows_clean_reregistration() -> None:
    bus = ExtensionEventBus()
    initial_handlers = dict(bus._handlers)
    trace: list[str] = []

    async def handler(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("handler")
        return await event_next(event)

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        trace.append("core")
        return "core"

    bus.register_event_type("demo.cascade", _matches, core)
    user_id = bus.register_event_handler("demo.cascade", handler, {}, HandlerLevel.USER)
    builtin_id = bus.register_event_handler("demo.cascade", handler, {}, HandlerLevel.BUILTIN)
    registry_key = _wrap_registry_key("demo.cascade")
    assert len(bus._framework.list_callbacks(registry_key)) == 2

    bus.unregister_event_type("demo.cascade")
    bus.unregister_event_handler(user_id)
    bus.unregister_event_handler(builtin_id)

    assert "demo.cascade" not in bus._events
    assert bus._handlers == initial_handlers
    assert bus._framework.list_callbacks(registry_key) == []

    bus.register_event_type("demo.cascade", _matches, core)
    dispatch = bus._events["demo.cascade"].dispatch
    assert (
        await dispatch(
            Event("demo.cascade", {}), matched_handler_ids=frozenset(), target_level=None, session_context=None
        )
        == "core"
    )
    assert trace == ["core"]
