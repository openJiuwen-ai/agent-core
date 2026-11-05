# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package methods through production bootstrap and per-handler Ops views."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock

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
    PackageMetadata,
    SessionContext,
)
from openjiuwen.core.events.builtins.handler_ops import package as package_ops

_BUILD = Event(OPS_BUILD_EVENT_TYPE, None)


def _matches(event_input: object, pattern: Mapping[str, object]) -> bool:
    return not pattern or (
        isinstance(event_input, Mapping) and all(event_input.get(key) == value for key, value in pattern.items())
    )


async def _core_echo(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
    del event, ops, event_next
    return "core"


async def _build_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    assert isinstance(await bus.trigger(_BUILD), EventHandlerOpsBuilder)
    return bus


async def _package_info(ops: EventHandlerOps | None) -> tuple[Path | None, str | None]:
    assert ops is not None
    return await ops.package.root(), await ops.package.name()


@pytest.mark.asyncio
async def test_package_bootstrap_build_only_registers_methods(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root_method = AsyncMock(wraps=package_ops.package_root)
    name_method = AsyncMock(wraps=package_ops.package_name)
    monkeypatch.setattr(package_ops, "package_root", root_method)
    monkeypatch.setattr(package_ops, "package_name", name_method)
    bus = ExtensionEventBus()
    other_calls: list[str] = []

    async def echo(ops: EventHandlerOps, value: str) -> str:
        del ops
        other_calls.append(value)
        return value

    async def add_other_ops(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        assert ops is None
        builder = await event_next(event)
        assert isinstance(builder, EventHandlerOpsBuilder)
        builder.add_namespace("example")
        builder.add_method("example", "echo", echo)
        return builder

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        assert ops is not None
        return await _package_info(ops), await ops.example.echo("other capability")

    bus.register_event_handler(OPS_BUILD_EVENT_TYPE, add_other_ops, {}, HandlerLevel.BUILTIN)
    bus.register_event_type("test.package", _matches, _core_echo)
    bus.register_event_handler(
        "test.package", handler, {}, package_metadata=PackageMetadata("demo.package", root=tmp_path)
    )
    assert isinstance(await bus.trigger(_BUILD), EventHandlerOpsBuilder)
    root_method.assert_not_awaited()
    name_method.assert_not_awaited()
    assert other_calls == []

    assert await bus.trigger(Event("test.package", {})) == ((tmp_path, "demo.package"), "other capability")
    root_method.assert_awaited_once()
    name_method.assert_awaited_once()
    assert other_calls == ["other capability"]


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [HandlerLevel.USER, HandlerLevel.BUILTIN])
@pytest.mark.parametrize("metadata_kind", ["complete", "id_only", "none"])
async def test_package_info_uses_metadata_for_each_layer(
    level: HandlerLevel, metadata_kind: str, tmp_path: Path
) -> None:
    bus = await _build_bus()
    root = tmp_path if metadata_kind == "complete" else None
    metadata = None if metadata_kind == "none" else PackageMetadata("demo.package", "1.0.0", root)
    observed: list[object] = []

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        observed.append(("handler", await _package_info(ops)))
        result = await event_next(event)
        observed.append(("after", await _package_info(ops)))
        return result

    async def core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        observed.append(("core", await _package_info(ops)))
        return "core"

    bus.register_event_type("test.package", _matches, core)
    bus.register_event_handler("test.package", handler, {}, level, package_metadata=metadata)

    assert await bus.trigger(Event("test.package", {})) == "core"
    expected = (root, None if metadata is None else metadata.package_id)
    assert observed == [("handler", expected), ("core", (None, None)), ("after", expected)]


@pytest.mark.asyncio
async def test_package_methods_preserve_directly_registered_values() -> None:
    bus = await _build_bus()
    metadata = PackageMetadata("my.custom.pkg", root=Path("relative/package"))

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        return await _package_info(ops)

    bus.register_event_type("test.package", _matches, _core_echo)
    bus.register_event_handler("test.package", handler, {}, package_metadata=metadata)

    assert await bus.trigger(Event("test.package", {})) == (metadata.root, "my.custom.pkg")


@pytest.mark.asyncio
async def test_package_not_inferred_from_event_or_session(tmp_path: Path) -> None:
    bus = await _build_bus()

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        return await _package_info(ops)

    bus.register_event_type("test.package", _matches, _core_echo)
    bus.register_event_handler("test.package", handler, {}, HandlerLevel.BUILTIN)
    session = SessionContext("session", str(tmp_path), {"package_id": "forged.package", "root": str(tmp_path)})

    result = await bus.trigger(Event("test.package", {"package_id": "forged.package", "root": tmp_path}), session)

    assert result == (None, None)


@pytest.mark.asyncio
async def test_package_methods_do_not_advance_chain_and_unmatched_handler_is_skipped(tmp_path: Path) -> None:
    bus = await _build_bus()
    calls: list[str] = []

    async def skipped(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, ops, event_next
        calls.append("unmatched")
        return "unmatched"

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        info = await _package_info(ops)
        assert info == (tmp_path, "demo.package")
        assert calls == []
        calls.append("handler")
        return await event_next(event)

    async def core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, ops, event_next
        calls.append("core")
        return "core"

    bus.register_event_type("test.package", _matches, core)
    bus.register_event_handler("test.package", skipped, {"kind": "other"})
    bus.register_event_handler(
        "test.package", handler, {}, package_metadata=PackageMetadata("demo.package", root=tmp_path)
    )

    assert await bus.trigger(Event("test.package", {"kind": "wanted"})) == "core"
    assert calls == ["handler", "core"]


@pytest.mark.asyncio
async def test_package_and_session_are_isolated_across_concurrent_chains(tmp_path: Path) -> None:
    bus = await _build_bus()
    first = PackageMetadata("first.package", root=tmp_path / "first")
    second = PackageMetadata("second.package", root=tmp_path / "second")
    sessions = [SessionContext("session-a"), SessionContext("session-b")]
    entered: list[str] = []
    ready = asyncio.Event()

    async def user(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        assert ops is not None and ops.session_context is not None
        before = await _package_info(ops), ops.session_context
        entered.append(ops.session_context.session_id)
        if len(entered) == 2:
            ready.set()
        await ready.wait()
        result = await event_next(event)
        return before, result, (await _package_info(ops), ops.session_context)

    async def builtin(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        assert ops is not None
        return (await _package_info(ops), ops.session_context), await event_next(event)

    async def core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        assert ops is not None
        return await _package_info(ops), ops.session_context

    bus.register_event_type("test.package", _matches, core)
    bus.register_event_handler("test.package", user, {}, package_metadata=first)
    bus.register_event_handler("test.package", builtin, {}, HandlerLevel.BUILTIN, package_metadata=second)

    results = await asyncio.wait_for(
        asyncio.gather(*(bus.trigger(Event("test.package", {}), session) for session in sessions)), timeout=5
    )

    for session, result in zip(sessions, results):
        expected_user = ((first.root, first.package_id), session)
        expected_builtin = ((second.root, second.package_id), session)
        assert result == (expected_user, (expected_builtin, ((None, None), session)), expected_user)


@pytest.mark.asyncio
async def test_nested_event_uses_its_own_handler_package(tmp_path: Path) -> None:
    bus = await _build_bus()
    session = SessionContext("nested-session")
    outer_package = PackageMetadata("outer.package", root=tmp_path / "outer")
    inner_package = PackageMetadata("inner.package", root=tmp_path / "inner")

    async def inner(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        assert ops is not None
        return await _package_info(ops), ops.session_context

    async def outer(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        assert ops is not None
        before = await _package_info(ops)
        nested = await bus.trigger(Event("test.inner", {}), ops.session_context)
        return before, nested, await _package_info(ops)

    bus.register_event_type("test.outer", _matches, _core_echo)
    bus.register_event_type("test.inner", _matches, _core_echo)
    bus.register_event_handler("test.outer", outer, {}, package_metadata=outer_package)
    bus.register_event_handler("test.inner", inner, {}, package_metadata=inner_package)

    assert await bus.trigger(Event("test.outer", {}), session) == (
        (outer_package.root, outer_package.package_id),
        ((inner_package.root, inner_package.package_id), session),
        (outer_package.root, outer_package.package_id),
    )


@pytest.mark.asyncio
async def test_package_call_requires_completed_build() -> None:
    bus = ExtensionEventBus()
    calls: list[object] = []

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        calls.append(await _package_info(ops))
        return calls[-1]

    bus.register_event_type("test.package", _matches, _core_echo)
    bus.register_event_handler("test.package", handler, {})

    with pytest.raises(ValidationError) as not_built:
        await bus.trigger(Event("test.package", {}))
    assert not_built.value.status is StatusCode.EVENTS_STATE_INVALID
    assert calls == []
    await bus.trigger(_BUILD)
    assert await bus.trigger(Event("test.package", {})) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["root", "name"])
async def test_package_methods_reject_extra_arguments(method_name: str) -> None:
    bus = await _build_bus()
    checked: list[str] = []

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, event_next
        assert ops is not None
        method = getattr(ops.package, method_name)
        with pytest.raises(TypeError):
            await method("unexpected")
        with pytest.raises(TypeError):
            await method(package_id="unexpected")
        checked.append(method_name)
        return await _package_info(ops)

    bus.register_event_type("test.package", _matches, _core_echo)
    bus.register_event_handler("test.package", handler, {})

    assert await bus.trigger(Event("test.package", {})) == (None, None)
    assert checked == [method_name]


@pytest.mark.asyncio
async def test_package_read_does_not_hide_downstream_failure() -> None:
    bus = await _build_bus()
    calls: list[str] = []
    failure = RuntimeError("core failed")

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        assert await _package_info(ops) == (None, None)
        return await event_next(event)

    async def core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del event, ops, event_next
        calls.append("core")
        raise failure

    bus.register_event_type("test.package", _matches, core)
    bus.register_event_handler("test.package", handler, {})

    with pytest.raises(RuntimeError) as caught:
        await bus.trigger(Event("test.package", {}))
    assert caught.value is failure
    assert calls == ["core"]


@pytest.mark.asyncio
async def test_cancel_after_package_read_propagates_and_exits_downstream() -> None:
    bus = await _build_bus()
    entered = asyncio.Event()
    stopped: list[str] = []

    async def handler(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        assert await _package_info(ops) == (None, None)
        return await event_next(event)

    async def core(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> None:
        del event, ops, event_next
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append("core exited")

    bus.register_event_type("test.package", _matches, core)
    bus.register_event_handler("test.package", handler, {}, HandlerLevel.BUILTIN)
    task = asyncio.create_task(bus.trigger(Event("test.package", {})))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped == ["core exited"]
