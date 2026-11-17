# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Store event contracts and methods through production bootstrap and Build."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOpsBuilder,
    ExtensionEventBus,
    HandlerLevel,
    PackageMetadata,
    SessionContext,
)
from openjiuwen.core.events.builtins.event_types.store import (
    StoreDeleteInput,
    StoreGetInput,
    StoreKeysInput,
    StoreSetInput,
    match_pattern_func,
)
from openjiuwen.core.events.ops_impl.store import configure_store
from openjiuwen.core.extension.data import JsonStore


async def _bus(tmp_path: Path) -> tuple[ExtensionEventBus, JsonStore]:
    service = JsonStore(tmp_path / "data")
    bus = ExtensionEventBus()
    configure_store(bus, service)
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(builder, EventHandlerOpsBuilder)
    assert not service.directory.exists()
    return bus, service


async def _invoke(bus, action, *, package_id="demo", session=None):
    async def core(event, ops, event_next):
        return "core"

    async def handler(event, ops, event_next):
        try:
            return await event.event_input(ops)
        except BaseError as exc:
            return exc

    bus.register_event_type("test.store_call", lambda value, pattern: True, core)
    bus.register_event_handler(
        "test.store_call",
        handler,
        {},
        package_metadata=None if package_id is None else PackageMetadata(package_id),
    )
    return await bus.trigger(Event("test.store_call", action), session)


@pytest.mark.asyncio
async def test_production_bootstrap_crud_and_real_ops(tmp_path: Path) -> None:
    bus, service = await _bus(tmp_path)
    context = SessionContext("session-a")
    observed: list[object] = []

    async def observe(event, ops, event_next):
        observed.append((event.event_input.package_id, ops.session_context))
        return await event_next(event)

    bus.register_event_handler("store.set", observe, {"package_id": "demo", "key": "config"})

    async def exercise(ops):
        assert await ops.package.name() == "demo"
        assert await ops.store.set("config", {"enabled": True}) is True
        assert await ops.store.get("config") == {"enabled": True}
        assert await ops.store.keys() == ["config"]
        sentinel = object()
        assert await ops.store.get("missing", sentinel) is sentinel
        assert await ops.store.delete("config") is True
        assert await ops.store.delete("config") is True
        return await ops.store.keys()

    assert await _invoke(bus, exercise, session=context) == []
    assert observed == [("demo", context)]
    assert await service.keys("demo") == []


@pytest.mark.asyncio
async def test_nested_store_call_preserves_original_chain_and_copies_input(tmp_path: Path) -> None:
    bus, service = await _bus(tmp_path)
    calls: list[str] = []
    source = {"value": []}

    async def intercept(event, ops, event_next):
        assert event.event_input.value is not source
        assert event.event_input.value["value"] is not source["value"]
        calls.append("store")
        event_input = replace(event.event_input, value={"value": [1]})
        return await event_next(Event("store.set", event_input))

    async def core(event, ops, event_next):
        calls.append("outer.core")
        return "done"

    async def handler(event, ops, event_next):
        assert await ops.store.set("key", source) is True
        assert calls == ["store"]
        return await event_next(event)

    bus.register_event_handler("store.set", intercept, {})
    bus.register_event_type("test.outer", lambda value, pattern: True, core)
    bus.register_event_handler("test.outer", handler, {}, package_metadata=PackageMetadata("demo"))
    assert await bus.trigger(Event("test.outer", None)) == "done"
    assert calls == ["store", "outer.core"]
    assert source == {"value": []}
    assert await service.get("demo", "key") == {"value": [1]}


@pytest.mark.asyncio
async def test_store_chain_continue_short_circuit_and_failed_output(tmp_path: Path, monkeypatch) -> None:
    bus, service = await _bus(tmp_path)
    setter = AsyncMock(wraps=service.set)
    monkeypatch.setattr(service, "set", setter)
    calls = []

    async def user(event, ops, event_next):
        calls.append("user")
        if event.event_input.key == "refused":
            return False
        result = await event_next(event)
        calls.append("user.result")
        return result

    async def builtin(event, ops, event_next):
        calls.append("builtin")
        if event.event_input.key == "invalid_output":
            return "invalid bool output"
        return await event_next(event)

    bus.register_event_handler("store.set", user, {}, HandlerLevel.USER)
    bus.register_event_handler("store.set", builtin, {}, HandlerLevel.BUILTIN)
    assert await bus.trigger(Event("store.set", StoreSetInput("demo", "normal", 1))) is True
    assert calls == ["user", "builtin", "user.result"]
    assert setter.await_count == 1
    calls.clear()
    assert await bus.trigger(Event("store.set", StoreSetInput("demo", "refused", 1))) is False
    assert calls == ["user"]
    assert setter.await_count == 1
    assert await bus.trigger(Event("store.set", StoreSetInput("demo", "invalid_output", 1))) is True
    assert setter.await_count == 2
    assert await service.keys("demo") == ["normal", "invalid_output"]


@pytest.mark.parametrize(
    "event_input", [StoreGetInput("demo", "x"), StoreSetInput("demo", "x", 1), StoreDeleteInput("demo", "x")]
)
def test_store_matchers_and_frozen_event_inputs(event_input) -> None:
    assert match_pattern_func(event_input, {})
    assert match_pattern_func(event_input, {"package_id": "demo", "key": "x"})
    assert not match_pattern_func(event_input, {"package_id": "other"})
    assert not match_pattern_func(event_input, {"key": "other"})
    for pattern in ({"unknown": "x"}, {"key": 1}, {"package_id": []}):
        with pytest.raises(BaseError) as caught:
            match_pattern_func(event_input, pattern)
        assert caught.value.status is StatusCode.EVENTS_MATCH_INVALID
    with pytest.raises(FrozenInstanceError):
        event_input.package_id = "other"


def test_keys_matcher_accepts_only_package_id() -> None:
    assert match_pattern_func(StoreKeysInput("demo"), {})
    assert match_pattern_func(StoreKeysInput("demo"), {"package_id": "demo"})
    assert not match_pattern_func(StoreKeysInput("demo"), {"package_id": "other"})
    assert not match_pattern_func(None, {})
    with pytest.raises(BaseError):
        match_pattern_func(StoreKeysInput("demo"), {"key": "x"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,event_input",
    [
        ("store.get", {}),
        ("store.get", StoreGetInput("../bad", "x")),
        ("store.set", StoreGetInput("demo", "x")),
        ("store.set", StoreSetInput("demo", "x", float("nan"))),
        ("store.delete", StoreDeleteInput("demo", 1)),
        ("store.keys", StoreKeysInput("../bad")),
    ],
)
async def test_event_input_validation_precedes_handlers(tmp_path: Path, event_type, event_input) -> None:
    bus, service = await _bus(tmp_path)
    handler = AsyncMock(return_value=True)
    bus.register_event_handler(event_type, handler, {})
    with pytest.raises(BaseError) as caught:
        await bus.trigger(Event(event_type, event_input))
    assert caught.value.status is StatusCode.EVENTS_PAYLOAD_INVALID
    handler.assert_not_awaited()
    assert not service.directory.exists()


@pytest.mark.asyncio
async def test_ops_package_and_session_isolation_without_a_session(tmp_path: Path) -> None:
    bus, service = await _bus(tmp_path)
    entered = asyncio.Event()
    calls = []

    async def core(event, ops, event_next):
        return None

    async def handler(event, ops, event_next):
        calls.append(await ops.package.name())
        if len(calls) == 2:
            entered.set()
        await entered.wait()
        assert await ops.store.set("key", ops.session_context.session_id if ops.session_context else "no-session")
        return await ops.store.get("key")

    bus.register_event_type("test.packages", lambda value, pattern: value == pattern["package"], core)
    for package_id in ("first", "second"):
        bus.register_event_handler(
            "test.packages",
            handler,
            {"package": package_id},
            package_metadata=PackageMetadata(package_id),
        )
    assert await asyncio.gather(
        bus.trigger(Event("test.packages", "first"), SessionContext("session-a")),
        bus.trigger(Event("test.packages", "second")),
    ) == ["session-a", "no-session"]
    assert await service.get("first", "key") == "session-a"
    assert await service.get("second", "key") == "no-session"


@pytest.mark.asyncio
async def test_store_rejects_unowned_ops_without_changing_package_name(tmp_path: Path) -> None:
    bus, service = await _bus(tmp_path)

    async def unowned(ops):
        assert await ops.package.name() is None
        return await ops.store.set("key", 1)

    result = await _invoke(bus, unowned, package_id=None)
    assert isinstance(result, BaseError)
    assert result.status is StatusCode.EVENTS_OPS_INVALID
    assert not service.directory.exists()


@pytest.mark.asyncio
async def test_core_failure_and_cancellation_reach_store_caller(tmp_path: Path, monkeypatch) -> None:
    bus, service = await _bus(tmp_path)
    assert await service.set("demo", "key", 1)
    (service.directory / "demo.json").write_text("corrupt", encoding="utf-8")

    async def read(ops):
        return await ops.store.get("key", "default")

    result = await _invoke(bus, read)
    assert isinstance(result, BaseError)
    assert result.status is StatusCode.EXTENSION_DATA_READ_FAILED
    bus2, service2 = await _bus(tmp_path / "cancel")
    entered = asyncio.Event()

    async def wait_forever(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service2, "keys", wait_forever)

    async def keys(ops):
        return await ops.store.keys()

    task = asyncio.create_task(_invoke(bus2, keys))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_unconfigured_bus_builds_but_store_access_fails() -> None:
    bus = ExtensionEventBus()
    assert isinstance(await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None)), EventHandlerOpsBuilder)
    with pytest.raises(BaseError) as caught:
        await bus.trigger(Event("store.keys", StoreKeysInput("demo")))
    assert caught.value.status is StatusCode.EXTENSION_DATA_INVALID
