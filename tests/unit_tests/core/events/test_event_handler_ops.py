# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior of the concrete event handler operations builder and view."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.types import PackageMetadata, SessionContext


async def _return_context(
    ops: EventHandlerOps,
    value: str,
    *,
    suffix: str,
) -> tuple[EventHandlerOps, PackageMetadata | None, SessionContext | None, str]:
    return ops, ops.package_metadata, ops.session_context, f"{value}{suffix}"


async def _noop(ops: EventHandlerOps) -> None:
    del ops


def _sync_method(ops: EventHandlerOps) -> None:
    del ops


class _BusinessFailure(Exception):
    """Representative extension business failure."""


@pytest.mark.asyncio
async def test_ops_happy_path_and_context_isolation() -> None:
    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    builder.add_method("tool", "call", _return_context)

    package_a = PackageMetadata("package-a", "1.0")
    package_b = PackageMetadata("package-b", "2.0")
    session_a = SessionContext("session-a", "/workspace/a")
    session_b = SessionContext("session-b", "/workspace/b")
    ops_a = builder.build(package_a, session_a)
    ops_b = builder.build(package_b, session_b)

    result_a, result_b = await asyncio.gather(
        ops_a.tool.call("A", suffix="-done"),
        ops_b.tool.call("B", suffix="-done"),
    )

    assert ops_a is not ops_b
    assert object.__getattribute__(ops_a, "_catalog") is object.__getattribute__(ops_b, "_catalog")
    assert result_a == (ops_a, package_a, session_a, "A-done")
    assert result_b == (ops_b, package_b, session_b, "B-done")
    assert ops_a.package_metadata is package_a
    assert ops_a.session_context is session_a
    assert ops_b.package_metadata is package_b
    assert ops_b.session_context is session_b


@pytest.mark.parametrize(
    ("operation", "name"),
    [
        ("namespace", 1),
        ("namespace", "not-valid"),
        ("namespace", "class"),
        ("namespace", "_private"),
        ("namespace", "package_metadata"),
        ("namespace", "session_context"),
        ("method", 1),
        ("method", "not-valid"),
        ("method", "class"),
        ("method", "_private"),
        ("duplicate_method", "call"),
        ("missing_namespace", "call"),
        ("sync_method", "call"),
    ],
)
def test_builder_rejects_invalid_registration(operation: str, name: object) -> None:
    builder = EventHandlerOpsBuilder()
    if operation != "namespace":
        builder.add_namespace("tool")
    if operation == "duplicate_method":
        builder.add_method("tool", "call", _noop)

    actions: dict[str, Callable[[], None]] = {
        "namespace": lambda: builder.add_namespace(name),  # type: ignore[arg-type]
        "method": lambda: builder.add_method("tool", name, _noop),  # type: ignore[arg-type]
        "duplicate_method": lambda: builder.add_method("tool", "call", _noop),
        "missing_namespace": lambda: builder.add_method("missing", "call", _noop),
        "sync_method": lambda: builder.add_method("tool", "call", _sync_method),  # type: ignore[arg-type]
    }

    with pytest.raises(ValidationError) as invalid:
        actions[operation]()
    assert invalid.value.status is StatusCode.EVENTS_OPS_INVALID


def test_shared_namespace_keeps_contributed_methods_unique() -> None:
    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    builder.add_method("tool", "call", _noop)
    builder.add_namespace("tool")
    builder.add_method("tool", "list", _noop)

    with pytest.raises(ValidationError) as invalid:
        builder.add_method("tool", "call", _noop)
    assert invalid.value.status is StatusCode.EVENTS_OPS_INVALID


@pytest.mark.parametrize("context", ["package_metadata", "session_context"])
def test_builder_rejects_invalid_build_context(context: str) -> None:
    builder = EventHandlerOpsBuilder()

    with pytest.raises(ValidationError) as invalid:
        builder.build(**{context: object()})  # type: ignore[arg-type]
    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["namespace", "method"])
async def test_unknown_capability_is_rejected(capability: str) -> None:
    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    ops = builder.build()

    with pytest.raises(ValidationError) as unknown:
        if capability == "namespace":
            _ = ops.missing
        else:
            await ops.tool.missing()
    assert unknown.value.status is StatusCode.EVENTS_OPS_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [
        pytest.param(_BusinessFailure("business failure"), id="business-failure"),
        pytest.param(asyncio.CancelledError("cancelled"), id="cancelled"),
    ],
)
async def test_ops_method_exceptions_propagate(exception: BaseException) -> None:
    async def fail(ops: EventHandlerOps, exception: BaseException) -> None:
        del ops
        raise exception

    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    builder.add_method("tool", "fail", fail)
    ops = builder.build()

    with pytest.raises(type(exception)) as raised:
        await ops.tool.fail(exception)
    assert raised.value is exception
