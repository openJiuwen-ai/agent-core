# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for AbortError functionality in callback framework.
"""

import pytest

from openjiuwen.core.runner.callback import (
    AsyncCallbackFramework,
    AbortError,
)


@pytest.fixture
def framework():
    """Create a fresh framework instance for each test."""
    return AsyncCallbackFramework(enable_metrics=True, enable_logging=False)


# === Section: AbortError with cause — trigger() re-raises cause ===


@pytest.mark.asyncio
async def test_abort_error_with_cause_reraises_cause(framework):
    """When AbortError carries a cause, trigger() re-raises the cause."""
    original = ValueError("original error")

    @framework.on("process")
    async def callback():
        raise AbortError("validation failed", cause=original)

    with pytest.raises(ValueError, match="original error"):
        await framework.trigger("process")


@pytest.mark.asyncio
async def test_abort_error_without_cause_reraises_abort_error(framework):
    """When AbortError has no cause, trigger() re-raises AbortError itself."""

    @framework.on("process")
    async def callback():
        raise AbortError("access denied")

    with pytest.raises(AbortError):
        await framework.trigger("process")


# === Section: AbortError stops subsequent callbacks ===


@pytest.mark.asyncio
async def test_abort_error_stops_subsequent_callbacks(framework):
    """AbortError in a high-priority callback stops lower-priority callbacks."""
    execution_order = []

    @framework.on("process", priority=10)
    async def first():
        execution_order.append("first")
        raise AbortError("stop here")

    @framework.on("process", priority=5)
    async def second():
        execution_order.append("second")
        return "second"

    with pytest.raises(AbortError):
        await framework.trigger("process")

    assert execution_order == ["first"]


# === Section: Normal Exception — continues to next callback ===


@pytest.mark.asyncio
async def test_normal_exception_does_not_stop_execution(framework):
    """A plain Exception is logged and swallowed; next callback still runs."""
    execution_order = []

    @framework.on("process", priority=10)
    async def failing():
        execution_order.append("first")
        raise RuntimeError("plain error")

    @framework.on("process", priority=5)
    async def succeeding():
        execution_order.append("second")
        return "ok"

    results = await framework.trigger("process")

    assert execution_order == ["first", "second"]
    assert results == ["ok"]


# === Section: Metrics recorded as error ===


@pytest.mark.asyncio
async def test_abort_error_records_error_metric(framework):
    """AbortError increments the error_count in metrics."""

    @framework.on("process")
    async def callback():
        raise AbortError("fail")

    with pytest.raises(AbortError):
        await framework.trigger("process")

    metrics = framework.get_metrics()
    assert metrics["process:callback"]["error_count"] == 1
    assert metrics["process:callback"]["call_count"] == 1


@pytest.mark.asyncio
async def test_normal_exception_records_error_metric(framework):
    """A plain Exception also increments error_count but execution continues."""

    @framework.on("process")
    async def callback():
        raise RuntimeError("oops")

    await framework.trigger("process")

    metrics = framework.get_metrics()
    assert metrics["process:callback"]["error_count"] == 1


# === Section: Circuit breaker records failure ===


@pytest.mark.asyncio
async def test_abort_error_triggers_circuit_breaker_failure(framework):
    """AbortError causes the circuit breaker to record a failure."""
    call_count = 0

    async def callback():
        nonlocal call_count
        call_count += 1
        raise AbortError("abort")

    framework.on("process")(callback)
    framework.add_circuit_breaker("process", callback, failure_threshold=1)

    with pytest.raises(AbortError):
        await framework.trigger("process")

    # Circuit breaker should be open now; second call filtered out
    results = await framework.trigger("process")
    assert results == []
    assert call_count == 1
