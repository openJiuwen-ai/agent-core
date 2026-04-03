# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for callback framework lifecycle hooks.
"""

import pytest

from openjiuwen.core.runner.callback import (
    HookType,
)


@pytest.mark.asyncio
async def test_before_hook_executes_before_callback(framework):
    """Test BEFORE hook executes before callback."""
    execution_order = []

    async def before_hook(*args, **kwargs):
        execution_order.append("before_hook")

    @framework.on("event")
    async def callback():
        execution_order.append("callback")

    framework.add_hook("event", HookType.BEFORE, before_hook)

    await framework.trigger("event")

    assert execution_order == ["before_hook", "callback"]


@pytest.mark.asyncio
async def test_before_hook_receives_args(framework):
    """Test BEFORE hook receives trigger arguments."""
    received = {}

    async def before_hook(*args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs

    @framework.on("event")
    async def callback(*args, **kwargs):
        pass

    framework.add_hook("event", HookType.BEFORE, before_hook)

    await framework.trigger("event", "arg1", key="value")

    assert received["args"] == ("arg1",)
    assert received["kwargs"] == {"key": "value"}


@pytest.mark.asyncio
async def test_multiple_before_hooks(framework):
    """Test multiple BEFORE hooks execute in order."""
    order = []

    async def hook1(*args, **kwargs):
        order.append("hook1")

    async def hook2(*args, **kwargs):
        order.append("hook2")

    @framework.on("event")
    async def callback():
        order.append("callback")

    framework.add_hook("event", HookType.BEFORE, hook1)
    framework.add_hook("event", HookType.BEFORE, hook2)

    await framework.trigger("event")

    assert order == ["hook1", "hook2", "callback"]


@pytest.mark.asyncio
async def test_after_hook_executes_after_callback(framework):
    """Test AFTER hook executes after callback."""
    execution_order = []

    async def after_hook(*args, **kwargs):
        execution_order.append("after_hook")

    @framework.on("event")
    async def callback():
        execution_order.append("callback")
        return "result"

    framework.add_hook("event", HookType.AFTER, after_hook)

    await framework.trigger("event")

    assert execution_order == ["callback", "after_hook"]


@pytest.mark.asyncio
async def test_after_hook_receives_results(framework):
    """Test AFTER hook receives callback results."""
    received_results = None

    async def after_hook(results, *args, **kwargs):
        nonlocal received_results
        received_results = results

    @framework.on("event")
    async def callback():
        return "result1"

    @framework.on("event")
    async def callback2():
        return "result2"

    framework.add_hook("event", HookType.AFTER, after_hook)

    await framework.trigger("event")

    assert received_results == ["result1", "result2"]


@pytest.mark.asyncio
async def test_error_hook_on_callback_exception(framework):
    """Test ERROR hook executes when callback throws."""
    error_received = None

    async def error_hook(error, *args, **kwargs):
        nonlocal error_received
        error_received = error

    @framework.on("event")
    async def failing_callback():
        raise ValueError("Test error")

    framework.add_hook("event", HookType.ERROR, error_hook)

    await framework.trigger("event")

    assert isinstance(error_received, ValueError)
    assert str(error_received) == "Test error"


@pytest.mark.asyncio
async def test_error_hook_receives_original_args(framework):
    """Test ERROR hook receives original trigger arguments."""
    received = {}

    async def error_hook(error, *args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs

    @framework.on("event")
    async def failing_callback(*args, **kwargs):
        raise RuntimeError("Error!")

    framework.add_hook("event", HookType.ERROR, error_hook)

    await framework.trigger("event", "arg1", key="value")

    assert received["args"] == ("arg1",)
    assert received["kwargs"] == {"key": "value", 'session': None}


@pytest.mark.asyncio
async def test_error_hook_called_for_each_error(framework):
    """Test ERROR hook is called for each failing callback."""
    error_count = 0

    async def error_hook(error, *args, **kwargs):
        nonlocal error_count
        error_count += 1

    @framework.on("event", priority=10)
    async def failing1():
        raise ValueError("Error 1")

    @framework.on("event", priority=5)
    async def failing2():
        raise ValueError("Error 2")

    framework.add_hook("event", HookType.ERROR, error_hook)

    await framework.trigger("event")

    assert error_count == 2


@pytest.mark.asyncio
async def test_cleanup_hook_in_trigger_generator(framework):
    """Test CLEANUP hook executes after trigger_generator completes."""
    execution_order = []

    async def cleanup_hook(*args, **kwargs):
        execution_order.append("cleanup")

    @framework.on("stream")
    async def generator():
        execution_order.append("generating")
        yield "item1"
        yield "item2"

    framework.add_hook("stream", HookType.CLEANUP, cleanup_hook)

    async for item in framework.trigger_generator("stream"):
        execution_order.append(f"received: {item}")

    assert "cleanup" in execution_order
    assert execution_order[-1] == "cleanup"


@pytest.mark.asyncio
async def test_sync_before_hook(framework):
    """Test synchronous BEFORE hook works."""
    called = False

    def sync_hook(*args, **kwargs):
        nonlocal called
        called = True

    @framework.on("event")
    async def callback():
        pass

    framework.add_hook("event", HookType.BEFORE, sync_hook)

    await framework.trigger("event")

    assert called


@pytest.mark.asyncio
async def test_sync_after_hook(framework):
    """Test synchronous AFTER hook works."""
    received_results = None

    def sync_hook(results, *args, **kwargs):
        nonlocal received_results
        received_results = results

    @framework.on("event")
    async def callback():
        return "result"

    framework.add_hook("event", HookType.AFTER, sync_hook)

    await framework.trigger("event")

    assert received_results == ["result"]


@pytest.mark.asyncio
async def test_hook_exception_does_not_stop_execution(framework):
    """Test hook exception doesn't prevent callback execution."""
    callback_executed = False

    async def failing_hook(*args, **kwargs):
        raise RuntimeError("Hook failed!")

    @framework.on("event")
    async def callback():
        nonlocal callback_executed
        callback_executed = True

    framework.add_hook("event", HookType.BEFORE, failing_hook)

    await framework.trigger("event")

    assert callback_executed


@pytest.mark.asyncio
async def test_after_hook_exception_logged(framework_with_logging, caplog):
    """Test hook exceptions are logged."""
    import logging

    async def failing_hook(*args, **kwargs):
        raise RuntimeError("Hook error!")

    @framework_with_logging.on("event")
    async def callback():
        return "result"

    framework_with_logging.add_hook("event", HookType.AFTER, failing_hook)

    with caplog.at_level(logging.ERROR):
        await framework_with_logging.trigger("event")

    # Hook failure should be logged
    assert any("Hook execution failed" in record.message for record in caplog.records)
