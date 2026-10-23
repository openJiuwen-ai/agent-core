# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Execution behavior for the internal EV-02 event bus."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, HandlerLevel, SessionContext


def _matches(event_input: object, pattern: Mapping[str, object]) -> bool:
    return isinstance(event_input, Mapping) and all(event_input.get(key) == value for key, value in pattern.items())


async def _pass_to_next(event: Event, ops: object | None, event_next: EventNext) -> object:
    del ops
    return await event_next(event)


async def _built_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "expected_status"),
    [
        (object(), StatusCode.EVENTS_PAYLOAD_INVALID),
        (Event("", {}), StatusCode.EVENTS_PAYLOAD_INVALID),
        (Event("demo.unknown", {}), StatusCode.EVENTS_UNKNOWN_EVENT_INVALID),
    ],
)
async def test_trigger_rejects_invalid_or_unknown_event(
    event: object,
    expected_status: StatusCode,
) -> None:
    bus = ExtensionEventBus()

    with pytest.raises(ValidationError) as invalid:
        await bus.trigger(event)
    assert invalid.value.status is expected_status


@pytest.mark.asyncio
async def test_trigger_dispatches_registered_core_without_handlers() -> None:
    bus = await _built_bus()
    bus.register_event_type("demo.core_only", _matches, _pass_to_next)

    assert await bus.trigger(Event("demo.core_only", {"value": 1}), session=SessionContext("session")) is None
    with pytest.raises(ValidationError) as unsupported_session:
        await bus.trigger(Event("demo.core_only", {"value": 1}), session=object())
    assert unsupported_session.value.status is StatusCode.EVENTS_PAYLOAD_INVALID


@pytest.mark.asyncio
async def test_complete_registration_and_layered_execution_smoke() -> None:
    bus = await _built_bus()
    trace: list[str] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        trace.append("core")
        return "core"

    async def builtin(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("builtin:enter")
        result = await event_next(event)
        trace.append("builtin:exit")
        return f"builtin({result})"

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("user:enter")
        result = await event_next(event)
        trace.append("user:exit")
        return f"user({result})"

    bus.register_event_type("demo.smoke", _matches, core)
    bus.register_event_handler("demo.smoke", _pass_to_next, {"selected": False}, HandlerLevel.USER)
    bus.register_event_handler("demo.smoke", user, {"selected": True}, HandlerLevel.USER)
    bus.register_event_handler("demo.smoke", builtin, {"selected": True}, HandlerLevel.BUILTIN)

    result = await bus.trigger(Event("demo.smoke", {"selected": True}))

    assert result == "user(builtin(core))"
    assert trace == ["user:enter", "builtin:enter", "core", "builtin:exit", "user:exit"]


@pytest.mark.asyncio
async def test_event_validators_run_in_execution_order() -> None:
    bus = await _built_bus()
    trace: list[str] = []

    def input_validator(event_input: object) -> bool:
        trace.append(f"input:{event_input}")
        return True

    def output_validator(event_output: object | None) -> bool:
        trace.append(f"output:{event_output}")
        return True

    def matcher(event_input: object, pattern: Mapping[str, object]) -> bool:
        del pattern
        trace.append("matcher")
        return event_input == "initial"

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops, event_next
        trace.append("core")
        return f"core:{event.event_input}"

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("user:enter")
        result = await event_next(Event(event.event_type, "forwarded"))
        trace.append("user:exit")
        return f"user:{result}"

    bus.register_event_type(
        "demo.validated_order",
        matcher,
        core,
        input_validator=input_validator,
        output_validator=output_validator,
    )
    bus.register_event_handler("demo.validated_order", user, {}, HandlerLevel.USER)

    assert await bus.trigger(Event("demo.validated_order", "initial")) == "user:core:forwarded"
    assert trace == [
        "input:initial",
        "matcher",
        "user:enter",
        "input:forwarded",
        "core",
        "output:core:forwarded",
        "user:exit",
        "output:user:core:forwarded",
    ]


@pytest.mark.asyncio
async def test_matching_preserves_layer_and_registration_order() -> None:
    bus = await _built_bus()
    trace: list[str] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        trace.append("core")
        return "done"

    def handler(name: str) -> Callable[[Event, object | None, EventNext], object]:
        async def run(event: Event, ops: object | None, event_next: EventNext) -> object:
            del ops
            trace.append(name)
            return await event_next(event)

        return run

    bus.register_event_type("demo.order", _matches, core)
    bus.register_event_handler("demo.order", handler("builtin"), {"kind": "match"}, HandlerLevel.BUILTIN)
    bus.register_event_handler("demo.order", handler("user-1"), {"kind": "match"}, HandlerLevel.USER)
    bus.register_event_handler("demo.order", handler("ignored"), {"kind": "other"}, HandlerLevel.USER)
    bus.register_event_handler("demo.order", handler("user-2"), {"kind": "match"}, HandlerLevel.USER)

    assert await bus.trigger(Event("demo.order", {"kind": "match"})) == "done"
    assert trace == ["user-1", "user-2", "builtin", "core"]


@pytest.mark.asyncio
async def test_matcher_contract_failure_stops_before_dispatch() -> None:
    def raising_matcher(event_input: object, pattern: Mapping[str, object]) -> bool:
        del event_input, pattern
        raise LookupError("matcher failed")

    def non_bool_matcher(event_input: object, pattern: Mapping[str, object]) -> object:
        del event_input, pattern
        return 1

    for index, matcher in enumerate((raising_matcher, non_bool_matcher)):
        invalid_bus = await _built_bus()
        invalid_trace: list[str] = []

        async def invalid_core(event: Event, ops: object | None, event_next: EventNext) -> object:
            del event, ops, event_next
            invalid_trace.append("core")
            return None

        async def invalid_handler(event: Event, ops: object | None, event_next: EventNext) -> object:
            del ops
            invalid_trace.append("handler")
            return await event_next(event)

        event_type = f"demo.invalid_matcher_{index}"
        invalid_bus.register_event_type(event_type, matcher, invalid_core)
        invalid_bus.register_event_handler(event_type, invalid_handler, {}, HandlerLevel.USER)

        with pytest.raises(ValidationError) as invalid:
            await invalid_bus.trigger(Event(event_type, {}))
        assert invalid.value.status is StatusCode.EVENTS_MATCH_INVALID
        assert invalid_trace == []


@pytest.mark.asyncio
@pytest.mark.parametrize("short_circuit", [False, True])
async def test_onion_return_and_short_circuit(short_circuit: bool) -> None:
    bus = await _built_bus()
    trace: list[str] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        trace.append("core")
        return "core"

    async def builtin(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("builtin:enter")
        result = await event_next(event)
        trace.append("builtin:exit")
        return f"builtin({result})"

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("user:enter")
        if short_circuit:
            return "short"
        result = await event_next(event)
        trace.append("user:exit")
        return f"user({result})"

    bus.register_event_type("demo.onion", _matches, core)
    bus.register_event_handler("demo.onion", user, {}, HandlerLevel.USER)
    bus.register_event_handler("demo.onion", builtin, {}, HandlerLevel.BUILTIN)

    result = await bus.trigger(Event("demo.onion", {}))

    if short_circuit:
        assert result == "short"
        assert trace == ["user:enter"]
    else:
        assert result == "user(builtin(core))"
        assert trace == ["user:enter", "builtin:enter", "core", "builtin:exit", "user:exit"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("jump_from", "target", "expected_trace", "is_invalid"),
    [
        ("user", HandlerLevel.BUILTIN, ["user", "builtin", "core"], False),
        ("user", HandlerLevel.CORE, ["user", "core"], False),
        ("builtin", HandlerLevel.CORE, ["user", "builtin", "core"], False),
        # An invalid jump is a handler fault: the USER handler is skipped and the chain continues.
        ("user", HandlerLevel.USER, ["user", "builtin", "core"], True),
    ],
)
async def test_layer_jumps_execute_only_the_allowed_range(
    jump_from: str,
    target: HandlerLevel,
    expected_trace: list[str],
    is_invalid: bool,
) -> None:
    bus = await _built_bus()
    trace: list[str] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        trace.append("core")
        return "core"

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("user")
        if jump_from == "user":
            return await event_next.to(event, target)
        return await event_next(event)

    async def skipped_user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("skipped-user")
        return await event_next(event)

    async def builtin(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append("builtin")
        if jump_from == "builtin":
            return await event_next.to(event, target)
        return await event_next(event)

    bus.register_event_type("demo.jump", _matches, core)
    bus.register_event_handler("demo.jump", user, {}, HandlerLevel.USER)
    if jump_from == "user" and not is_invalid:
        bus.register_event_handler("demo.jump", skipped_user, {}, HandlerLevel.USER)
    bus.register_event_handler("demo.jump", builtin, {}, HandlerLevel.BUILTIN)

    assert await bus.trigger(Event("demo.jump", {})) == "core"
    assert trace == expected_trace


async def _assert_event_next_rejected(mode: str) -> None:
    bus = await _built_bus()
    captured: list[EventNext] = []
    rejected: list[ValidationError] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "core"

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        captured.append(event_next)
        try:
            if mode == "cross-task":
                return await asyncio.create_task(event_next(event))
            if mode == "changed-type":
                return await event_next(Event("demo.other", event.event_input))
        except ValidationError as error:
            rejected.append(error)
            raise
        return "short"

    bus.register_event_type("demo.next", _matches, core)
    bus.register_event_handler("demo.next", user, {}, HandlerLevel.USER)

    if mode == "expired":
        assert await bus.trigger(Event("demo.next", {})) == "short"
        with pytest.raises(ValidationError) as invalid:
            await captured[0](Event("demo.next", {}))
        rejected.append(invalid.value)
    else:
        # The rejection reaches the handler; the failed handler is then skipped.
        assert await bus.trigger(Event("demo.next", {})) == "core"
    assert [error.status for error in rejected] == [StatusCode.EVENTS_NEXT_INVALID]


@pytest.mark.asyncio
async def test_event_next_constraints_and_input_replacement_do_not_rematch() -> None:
    for mode in ("cross-task", "expired", "changed-type"):
        await _assert_event_next_rejected(mode)

    bus = await _built_bus()
    matcher_inputs: list[object] = []

    def matcher(event_input: object, pattern: Mapping[str, object]) -> bool:
        matcher_inputs.append(event_input)
        return _matches(event_input, pattern)

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops, event_next
        return event.event_input

    async def user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        return await event_next(Event(event.event_type, {"kind": "changed"}))

    bus.register_event_type("demo.input", matcher, core)
    bus.register_event_handler("demo.input", user, {"kind": "initial"}, HandlerLevel.USER)

    assert await bus.trigger(Event("demo.input", {"kind": "initial"})) == {"kind": "changed"}
    assert matcher_inputs == [{"kind": "initial"}]


@pytest.mark.asyncio
async def test_inflight_call_keeps_old_handlers_while_new_call_sees_updates() -> None:
    bus = await _built_bus()
    entered = asyncio.Event()
    release = asyncio.Event()
    trace: list[str] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops, event_next
        trace.append(f"{event.event_input['call']}:core")
        return "core"

    async def first_user(event: Event, ops: object | None, event_next: EventNext) -> object:
        del ops
        trace.append(f"{event.event_input['call']}:u1")
        entered.set()
        await release.wait()
        return await event_next(event)

    def later_user(name: str) -> Callable[[Event, object | None, EventNext], object]:
        async def run(event: Event, ops: object | None, event_next: EventNext) -> object:
            del ops
            trace.append(f"{event.event_input['call']}:{name}")
            return await event_next(event)

        return run

    bus.register_event_type("demo.snapshot", _matches, core)
    bus.register_event_handler("demo.snapshot", first_user, {}, HandlerLevel.USER)
    old_handler_id = bus.register_event_handler("demo.snapshot", later_user("u2"), {}, HandlerLevel.USER)

    old_call = asyncio.create_task(bus.trigger(Event("demo.snapshot", {"call": "old"})))
    await entered.wait()
    bus.unregister_event_handler(old_handler_id)
    bus.register_event_handler("demo.snapshot", later_user("u3"), {}, HandlerLevel.USER)
    release.set()

    assert await old_call == "core"
    assert await bus.trigger(Event("demo.snapshot", {"call": "new"})) == "core"
    assert trace == ["old:u1", "old:u2", "old:core", "new:u1", "new:u3", "new:core"]


@pytest.mark.asyncio
async def test_handler_cancellation_propagates_unchanged() -> None:
    bus = await _built_bus()
    captured: list[EventNext] = []

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "unexpected"

    async def failing_handler(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops
        captured.append(event_next)
        raise asyncio.CancelledError("cancelled")

    bus.register_event_type("demo.failure", _matches, core)
    bus.register_event_handler("demo.failure", failing_handler, {}, HandlerLevel.USER)

    with pytest.raises(asyncio.CancelledError, match="cancelled"):
        await bus.trigger(Event("demo.failure", {}))

    with pytest.raises(ValidationError) as invalid:
        await captured[0](Event("demo.failure", {}))
    assert invalid.value.status is StatusCode.EVENTS_NEXT_INVALID


@pytest.mark.asyncio
async def test_non_awaitable_handler_is_skipped() -> None:
    bus = await _built_bus()

    async def core(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "unexpected"

    def non_awaitable_handler(event: Event, ops: object | None, event_next: EventNext) -> object:
        del event, ops, event_next
        return "not-awaitable"

    bus.register_event_type("demo.non_awaitable_handler", _matches, core)
    bus.register_event_handler("demo.non_awaitable_handler", non_awaitable_handler, {}, HandlerLevel.USER)

    assert await bus.trigger(Event("demo.non_awaitable_handler", {})) == "unexpected"
