# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A host allowlist narrows candidates without changing normal chain rules."""

import asyncio

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events import Event, ExtensionEventBus, HandlerLevel, PackageMetadata, SessionContext


async def _bus():
    bus = ExtensionEventBus()
    await bus.trigger(Event("ops.build", None))
    return bus


@pytest.mark.asyncio
async def test_allowlist_preserves_layers_order_matchers_and_bound_facts():
    bus = await _bus()
    trace = []
    matched = []

    def matcher(value, pattern):
        matched.append(pattern["label"])
        return value == pattern.get("value", value)

    async def core(event, ops, next_handler):
        trace.append("core")
        assert ops.session_context.session_id == "session-a"
        return "result"

    def handler(label):
        async def run(event, ops, next_handler):
            trace.append(label)
            assert ops.package_metadata.package_id == "package-a"
            return await next_handler(event)
        return run

    bus.register_event_type("demo.selected", matcher, core)
    bus.register_event_type("demo.other", matcher, core)
    package = PackageMetadata("package-a")
    user = bus.register_event_handler("demo.selected", handler("user"), {"label": "user"}, package_metadata=package)
    builtin = bus.register_event_handler("demo.selected", handler("builtin"), {"label": "builtin"}, HandlerLevel.BUILTIN, package_metadata=package)
    bus.register_event_handler("demo.selected", handler("excluded"), {"label": "excluded"}, package_metadata=package)
    other = bus.register_event_handler("demo.other", handler("other"), {"label": "other"}, package_metadata=package)
    mismatch = bus.register_event_handler("demo.selected", handler("mismatch"), {"label": "mismatch", "value": "other"}, package_metadata=package)
    result = await bus.trigger(Event("demo.selected", "input"), SessionContext("session-a"),
                               handler_ids=[builtin, other, user, user, "unknown", mismatch])
    assert result == "result" and trace == ["user", "builtin", "core"]
    assert matched == ["user", "builtin", "mismatch"]
    trace.clear()
    await bus.trigger(Event("demo.selected", "input"), SessionContext("session-a"))
    assert trace == ["user", "excluded", "builtin", "core"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], ["unknown"]])
async def test_empty_or_unknown_allowlist_keeps_the_core_tail(ids):
    bus = await _bus()
    trace = []

    async def core(event, ops, next_handler):
        trace.append("core")
        return 0

    async def user(event, ops, next_handler):
        trace.append("user")
        return await next_handler(event)

    bus.register_event_type("demo.only_core", lambda value, pattern: True, core)
    bus.register_event_handler("demo.only_core", user, {})
    assert await bus.trigger(Event("demo.only_core", None), handler_ids=ids) == 0
    assert trace == ["core"]


@pytest.mark.asyncio
async def test_allowlist_and_registration_are_fixed_before_the_first_await():
    bus = await _bus()
    entered, release = asyncio.Event(), asyncio.Event()
    trace = []

    async def core(event, ops, next_handler):
        trace.append("core")
        return "done"

    async def first(event, ops, next_handler):
        trace.append("first")
        entered.set()
        await release.wait()
        return await next_handler(event)

    async def last(event, ops, next_handler):
        trace.append("last")
        return await next_handler(event)

    bus.register_event_type("demo.snapshot", lambda value, pattern: True, core)
    a = bus.register_event_handler("demo.snapshot", first, {})
    b = bus.register_event_handler("demo.snapshot", last, {})
    ids = [a, b]
    task = asyncio.create_task(bus.trigger(Event("demo.snapshot", None), handler_ids=ids))
    await entered.wait()
    ids.clear()
    bus.unregister_event_handler(b)
    ids.append(bus.register_event_handler("demo.snapshot", last, {}))
    release.set()
    assert await task == "done"
    assert trace == ["first", "last", "core"]


@pytest.mark.asyncio
async def test_selected_handler_failure_keeps_recovery_inside_the_allowlist():
    bus = await _bus()
    trace = []

    async def core(event, ops, next_handler):
        trace.append("core")
        return "ok"

    async def broken(event, ops, next_handler):
        trace.append("broken")
        raise RuntimeError("user failure")

    async def excluded(event, ops, next_handler):
        trace.append("excluded")
        return await next_handler(event)

    bus.register_event_type("demo.recover", lambda value, pattern: True, core)
    selected = bus.register_event_handler("demo.recover", broken, {})
    bus.register_event_handler("demo.recover", excluded, {})
    assert await bus.trigger(Event("demo.recover", None), handler_ids=[selected]) == "ok"
    assert trace == ["broken", "core"]


@pytest.mark.asyncio
async def test_selected_short_circuit_and_external_cancel_keep_existing_semantics():
    bus = await _bus()
    entered = asyncio.Event()

    async def core(event, ops, next_handler):
        raise AssertionError("CORE must not run after a valid short circuit")

    async def selected(event, ops, next_handler):
        if event.event_input == "return":
            return "short"
        entered.set()
        await asyncio.Event().wait()

    bus.register_event_type("demo.stop", lambda value, pattern: True, core)
    selected_id = bus.register_event_handler("demo.stop", selected, {})
    assert await bus.trigger(Event("demo.stop", "return"), handler_ids=[selected_id]) == "short"
    task = asyncio.create_task(bus.trigger(Event("demo.stop", "wait"), handler_ids=[selected_id]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["id", b"id", {"id"}, 1, [None], [""], [False]])
async def test_invalid_allowlist_is_a_payload_error(invalid):
    bus = await _bus()

    async def core(event, ops, next_handler):
        raise AssertionError("invalid allowlist must fail before dispatch")

    bus.register_event_type("demo.invalid", lambda value, pattern: True, core)
    with pytest.raises(ValidationError) as error:
        await bus.trigger(Event("demo.invalid", None), handler_ids=invalid)
    assert error.value.status is StatusCode.EVENTS_PAYLOAD_INVALID


@pytest.mark.asyncio
async def test_build_rejects_a_restricted_catalog_without_consuming_build():
    bus = ExtensionEventBus()
    with pytest.raises(ValidationError) as error:
        await bus.trigger(Event("ops.build", None), handler_ids=[])
    assert error.value.status is StatusCode.EVENTS_PAYLOAD_INVALID
    assert await bus.trigger(Event("ops.build", None)) is not None
