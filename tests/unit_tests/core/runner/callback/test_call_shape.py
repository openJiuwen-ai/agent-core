# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests and a local microbenchmark for cached callback call shapes."""

import asyncio
import inspect
import statistics
import time

import pytest

from openjiuwen.core.runner.callback.framework import (
    AsyncCallbackFramework,
)
from openjiuwen.core.runner.callback.models import CallbackInfo


@pytest.mark.asyncio
async def test_trigger_uses_call_shape_cached_at_registration(monkeypatch):
    framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)

    async def callback(value, *, label, **kwargs):
        return value, label, kwargs

    callback_info = framework.register_sync("event", callback)
    assert callback_info.call_shape is not None

    def fail_signature_lookup(_callback):
        raise AssertionError("trigger must not analyze a registered callback")

    monkeypatch.setattr(
        "openjiuwen.core.runner.callback.framework._get_signature",
        fail_signature_lookup,
    )
    monkeypatch.setattr(
        "openjiuwen.core.runner.callback.framework.inspect.signature",
        fail_signature_lookup,
    )

    results = await framework.trigger("event", 7, "ignored", label="cached", extra=True)

    assert results == [(7, "cached", {"extra": True, "session": None})]


@pytest.mark.asyncio
async def test_trigger_transform_uses_cached_call_shape(monkeypatch):
    framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)

    async def transform(value):
        return value * 2

    callback_info = framework.register_sync("event", transform, callback_type="transform")
    assert callback_info.call_shape is not None

    def fail_signature_lookup(_callback):
        raise AssertionError("trigger_transform must use the cached call shape")

    monkeypatch.setattr(
        "openjiuwen.core.runner.callback.framework._get_signature",
        fail_signature_lookup,
    )
    monkeypatch.setattr(
        "openjiuwen.core.runner.callback.framework.inspect.signature",
        fail_signature_lookup,
    )

    assert await framework.trigger_transform("event", 4, "ignored", extra=True) == 8


@pytest.mark.asyncio
async def test_trigger_supports_callback_info_without_cached_shape():
    framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)

    async def callback(value):
        return value

    framework.callbacks["event"].append(CallbackInfo(callback=callback, priority=0))

    assert await framework.trigger("event", 7, "ignored", extra=True) == [7]


@pytest.mark.asyncio
async def test_trigger_rebuilds_shape_when_callback_changes():
    framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)

    async def original(value):
        return value

    callback_info = framework.register_sync("event", original)

    async def replacement(value, *, label):
        return value, label

    callback_info.callback = replacement

    assert await framework.trigger("event", 7, label="updated") == [(7, "updated")]


@pytest.mark.asyncio
async def test_trigger_rebuilds_shape_when_custom_signature_changes():
    framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)

    async def callback(*args, **kwargs):
        return args, kwargs

    setattr(
        callback,
        "__signature__",
        inspect.Signature([inspect.Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD)]),
    )
    framework.register_sync("event", callback)
    setattr(
        callback,
        "__signature__",
        inspect.Signature(
            [
                inspect.Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                inspect.Parameter("label", inspect.Parameter.KEYWORD_ONLY),
            ]
        ),
    )

    assert await framework.trigger("event", 7, "ignored", label="updated") == [((7,), {"label": "updated"})]


def test_register_accepts_unhashable_callable():
    framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)

    class Callback:
        __hash__ = None

        async def __call__(self, value):
            return value

    callback_info = framework.register_sync("event", Callback())

    assert callback_info.call_shape is not None


async def _run_microbenchmark() -> tuple[float, float]:
    async def callback(value, *, label, **kwargs):
        return value, label, kwargs

    legacy_framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)
    legacy_info = legacy_framework.register_sync("event", callback)
    legacy_info.call_shape = None

    cached_framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)
    cached_framework.register_sync("event", callback)

    session = object()
    await legacy_framework.trigger("event", 7, "ignored", label="cached", session=session)
    await cached_framework.trigger("event", 7, "ignored", label="cached", session=session)

    iterations = 20_000
    legacy_samples = []
    cached_samples = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(iterations):
            legacy_info.call_shape = None
            await legacy_framework.trigger("event", 7, "ignored", label="cached", session=session)
        legacy_samples.append(time.perf_counter() - start)

        start = time.perf_counter()
        for _ in range(iterations):
            await cached_framework.trigger("event", 7, "ignored", label="cached", session=session)
        cached_samples.append(time.perf_counter() - start)

    return statistics.median(legacy_samples), statistics.median(cached_samples)


if __name__ == "__main__":
    before, after = asyncio.run(_run_microbenchmark())
    print(f"legacy={before:.6f}s cached={after:.6f}s speedup={before / after:.2f}x")
