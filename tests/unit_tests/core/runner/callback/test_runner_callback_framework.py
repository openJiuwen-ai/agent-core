# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for callback framework usage through Runner.
"""

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback import (
    ChainAction,
    ChainResult,
    RateLimitFilter,
    ValidationFilter,
    HookType,
)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_callbacks():
    """Clean up all registered callbacks after each test."""
    yield
    # Cleanup: unregister all callbacks registered during the test
    framework = Runner.callback_framework
    # Get all events that have callbacks (create a copy of the list to avoid modification during iteration)
    events_to_clean = list(framework.callbacks.keys())
    for event in events_to_clean:
        # Use unregister_event to clean up everything for this event
        await framework.unregister_event(event)


@pytest.mark.asyncio
async def test_runner_callback_framework_property():
    """Test that global Runner instance provides callback_framework property."""
    framework = Runner.callback_framework
    assert framework is not None
    assert hasattr(framework, 'register')
    assert hasattr(framework, 'trigger')
    assert hasattr(framework, 'on')


@pytest.mark.asyncio
async def test_runner_callback_framework_register_and_trigger():
    """Test registering and triggering callbacks through Runner's callback_framework."""
    framework = Runner.callback_framework
    call_log = []

    @framework.on("test_event")
    async def handler(message: str):
        call_log.append(f"received: {message}")
        return f"processed: {message}"

    results = await framework.trigger("test_event", message="hello")
    assert len(results) == 1
    assert results[0] == "processed: hello"
    assert call_log == ["received: hello"]


@pytest.mark.asyncio
async def test_runner_callback_framework_multiple_callbacks():
    """Test multiple callbacks with priority through Runner's callback_framework."""
    framework = Runner.callback_framework
    execution_order = []

    @framework.on("event", priority=1)
    async def low_priority():
        execution_order.append("low")
        return "low_result"

    @framework.on("event", priority=10)
    async def high_priority():
        execution_order.append("high")
        return "high_result"

    results = await framework.trigger("event")
    assert results == ["high_result", "low_result"]
    assert execution_order == ["high", "low"]


@pytest.mark.asyncio
async def test_runner_callback_framework_with_filters():
    """Test callback framework with filters through Runner."""
    framework = Runner.callback_framework
    validator = ValidationFilter(lambda **kwargs: kwargs.get('value', 0) > 0)

    @framework.on("event", filters=[validator])
    async def callback(value: int):
        return value * 2

    # Valid call should work
    results = await framework.trigger("event", value=10)
    assert results == [20]

    # Invalid call should be filtered out
    results = await framework.trigger("event", value=-5)
    assert results == []


@pytest.mark.asyncio
async def test_runner_callback_framework_rate_limit():
    """Test rate limiting through Runner's callback_framework."""
    framework = Runner.callback_framework
    rate_limit = RateLimitFilter(max_calls=2, time_window=1.0)
    call_count = 0

    @framework.on("event", filters=[rate_limit])
    async def callback():
        nonlocal call_count
        call_count += 1
        return call_count

    # First two calls should succeed
    results1 = await framework.trigger("event")
    results2 = await framework.trigger("event")
    assert results1 == [1]
    assert results2 == [2]

    # Third call should be rate limited
    results3 = await framework.trigger("event")
    assert results3 == []


@pytest.mark.asyncio
async def test_runner_callback_framework_decorators():
    """Test using decorators through Runner's callback_framework."""
    framework = Runner.callback_framework
    event_log = []

    @framework.on("before_event")
    async def before_handler(*args, **kwargs):
        event_log.append("before")

    @framework.trigger_on_call("before_event", pass_args=False)
    async def process_data(data: str):
        event_log.append(f"process: {data}")
        return {"result": data}

    result = await process_data("test")
    assert result == {"result": "test"}
    assert event_log == ["before", "process: test"]


@pytest.mark.asyncio
async def test_runner_callback_framework_chain():
    """Test callback chain through Runner's callback_framework."""
    framework = Runner.callback_framework
    rollback_called = False

    @framework.on("chain_event")
    async def callback(**kwargs):
        return ChainResult(ChainAction.ROLLBACK, error=Exception("fail"))

    async def rollback_handler(context):
        nonlocal rollback_called
        rollback_called = True

    await framework.register(
        "chain_event",
        callback,
        rollback_handler=rollback_handler
    )

    result = await framework.trigger_chain("chain_event")
    assert result.action == ChainAction.ROLLBACK


@pytest.mark.asyncio
async def test_runner_callback_framework_hooks():
    """Test hooks through Runner's callback_framework."""
    framework = Runner.callback_framework
    execution_order = []

    async def before_hook(*args, **kwargs):
        execution_order.append("before_hook")

    @framework.on("event")
    async def callback():
        execution_order.append("callback")
        return "result"

    async def after_hook(results, *args, **kwargs):
        execution_order.append("after_hook")
        assert results == ["result"]

    framework.add_hook("event", HookType.BEFORE, before_hook)
    framework.add_hook("event", HookType.AFTER, after_hook)

    results = await framework.trigger("event")
    assert results == ["result"]
    assert execution_order == ["before_hook", "callback", "after_hook"]


@pytest.mark.asyncio
async def test_runner_callback_framework_namespace():
    """Test namespace isolation through Runner's callback_framework."""
    framework = Runner.callback_framework

    @framework.on("event", namespace="ns1")
    async def callback1():
        return "ns1_result"

    @framework.on("event", namespace="ns2")
    async def callback2():
        return "ns2_result"

    results = await framework.trigger("event")
    assert len(results) == 2
    assert "ns1_result" in results
    assert "ns2_result" in results


@pytest.mark.asyncio
async def test_runner_callback_framework_unregister():
    """Test unregistering callbacks through Runner's callback_framework."""
    framework = Runner.callback_framework

    async def callback1():
        return "result1"

    async def callback2():
        return "result2"

    await framework.register("event", callback1)
    await framework.register("event", callback2)

    assert len(framework.list_callbacks("event")) == 2

    await framework.unregister("event", callback1)
    callbacks = framework.list_callbacks("event")
    assert len(callbacks) == 1
    assert callbacks[0]["name"] == "callback2"


@pytest.mark.asyncio
async def test_runner_callback_framework_unregister_decorator_callback():
    """Test unregistering callback registered with @framework.on decorator."""
    framework = Runner.callback_framework

    # Register callbacks using decorator
    @framework.on("event")
    async def callback1():
        return "result1"

    @framework.on("event")
    async def callback2():
        return "result2"

    assert len(framework.list_callbacks("event")) == 2

    # Method 1: Unregister using the decorator wrapper (NEW - supported now)
    # This is the most convenient way - just use the decorated function directly
    await framework.unregister("event", callback1)
    callbacks = framework.list_callbacks("event")
    assert len(callbacks) == 1
    assert callbacks[0]["name"] == "callback2"

    # Method 2: Unregister using the other decorator wrapper
    await framework.unregister("event", callback2)
    callbacks = framework.list_callbacks("event")
    assert len(callbacks) == 0

    # Test with multiple callbacks
    @framework.on("test_event")
    async def callback3():
        return "result3"

    @framework.on("test_event")
    async def callback4():
        return "result4"

    assert len(framework.list_callbacks("test_event")) == 2

    # Unregister one using wrapper
    await framework.unregister("test_event", callback3)
    callbacks = framework.list_callbacks("test_event")
    assert len(callbacks) == 1
    assert callbacks[0]["name"] == "callback4"

    # Unregister the remaining one
    await framework.unregister("test_event", callback4)
    callbacks = framework.list_callbacks("test_event")
    assert len(callbacks) == 0


@pytest.mark.asyncio
async def test_runner_callback_framework_unregister_event():
    """Test unregistering all callbacks for an event."""
    framework = Runner.callback_framework

    @framework.on("test_event")
    async def callback1():
        return "result1"

    @framework.on("test_event")
    async def callback2():
        return "result2"

    @framework.on("other_event")
    async def callback3():
        return "result3"

    # Verify callbacks are registered
    assert len(framework.list_callbacks("test_event")) == 2
    assert len(framework.list_callbacks("other_event")) == 1

    # Unregister all callbacks for test_event
    await framework.unregister_event("test_event")

    # Verify test_event callbacks are removed
    assert len(framework.list_callbacks("test_event")) == 0
    # Verify other_event callbacks are still there
    assert len(framework.list_callbacks("other_event")) == 1

    # Verify triggering test_event returns no results
    results = await framework.trigger("test_event")
    assert results == []

    # Verify other_event still works
    results = await framework.trigger("other_event")
    assert results == ["result3"]


@pytest.mark.asyncio
async def test_runner_callback_framework_unregister_event_with_filters():
    """Test unregister_event also removes filters."""
    framework = Runner.callback_framework
    validator = ValidationFilter(lambda **kwargs: kwargs.get('value', 0) > 0)

    @framework.on("test_event", filters=[validator])
    async def callback(value: int):
        return value * 2

    # Add event-specific filter
    framework.add_filter("test_event", validator)

    # Verify callback works
    results = await framework.trigger("test_event", value=10)
    assert results == [20]

    # Unregister event
    await framework.unregister_event("test_event")

    # Verify filters are also removed
    assert "test_event" not in framework._filters
    assert len(framework.list_callbacks("test_event")) == 0


@pytest.mark.asyncio
async def test_runner_callback_framework_unregister_event_with_chain():
    """Test unregister_event also removes chains."""
    framework = Runner.callback_framework

    @framework.on("chain_event")
    async def callback(**kwargs):
        return ChainResult(ChainAction.CONTINUE)

    async def rollback_handler(context):
        pass

    await framework.register(
        "chain_event",
        callback,
        rollback_handler=rollback_handler
    )

    # Verify chain exists
    assert "chain_event" in framework.chains

    # Unregister event
    await framework.unregister_event("chain_event")

    # Verify chain is removed
    assert "chain_event" not in framework.chains
    assert len(framework.list_callbacks("chain_event")) == 0


@pytest.mark.asyncio
async def test_runner_callback_framework_unregister_event_with_hooks():
    """Test unregister_event also removes hooks."""
    framework = Runner.callback_framework

    @framework.on("test_event")
    async def callback():
        return "result"

    async def before_hook(*args, **kwargs):
        pass

    async def after_hook(results, *args, **kwargs):
        pass

    framework.add_hook("test_event", HookType.BEFORE, before_hook)
    framework.add_hook("test_event", HookType.AFTER, after_hook)

    # Verify hooks exist (check that the event is in _hooks and has the hook types)
    assert "test_event" in framework._hooks
    assert HookType.BEFORE in framework._hooks["test_event"]
    assert HookType.AFTER in framework._hooks["test_event"]
    assert len(framework._hooks["test_event"][HookType.BEFORE]) > 0
    assert len(framework._hooks["test_event"][HookType.AFTER]) > 0

    # Unregister event
    await framework.unregister_event("test_event")

    # Verify hooks are removed
    assert "test_event" not in framework._hooks
    assert len(framework.list_callbacks("test_event")) == 0


@pytest.mark.asyncio
async def test_runner_callback_framework_unregister_nonexistent_event():
    """Test unregister_event on nonexistent event does not error."""
    framework = Runner.callback_framework

    # Should not raise an error
    await framework.unregister_event("nonexistent_event")

    # Verify no callbacks exist
    assert len(framework.list_callbacks("nonexistent_event")) == 0


@pytest.mark.asyncio
async def test_runner_callback_framework_tags():
    """Test callback tags through Runner's callback_framework."""
    framework = Runner.callback_framework

    @framework.on("event", tags={"debug", "test"})
    async def debug_callback():
        return "debug_result"

    @framework.on("event", tags={"production"})
    async def prod_callback():
        return "prod_result"

    callbacks = framework.list_callbacks("event")
    assert len(callbacks) == 2
    tags_set = {tag for cb in callbacks for tag in cb["tags"]}
    assert "debug" in tags_set
    assert "test" in tags_set
    assert "production" in tags_set


@pytest.mark.asyncio
async def test_runner_callback_framework_emit_around():
    """Test emit_around decorator through Runner's callback_framework."""
    framework = Runner.callback_framework
    event_log = []

    @framework.on("start")
    async def on_start():
        event_log.append("start")

    @framework.on("end")
    async def on_end(result):
        event_log.append(f"end: {result}")

    @framework.emit_around("start", "end")
    async def process():
        event_log.append("processing")
        return "done"

    result = await process()
    assert result == "done"
    assert event_log == ["start", "processing", "end: done"]


@pytest.mark.asyncio
async def test_runner_callback_framework_emits():
    """Test emits decorator through Runner's callback_framework."""
    framework = Runner.callback_framework
    received_results = []

    @framework.on("data_ready")
    async def on_ready(result):
        received_results.append(result)

    @framework.emits("data_ready")
    async def process():
        return {"status": "done"}

    result = await process()
    assert result == {"status": "done"}
    assert received_results == [{"status": "done"}]


@pytest.mark.asyncio
async def test_runner_callback_framework_error_handling():
    """Test error handling through Runner's callback_framework."""
    framework = Runner.callback_framework
    error_received = None

    async def error_handler(error, context):
        nonlocal error_received
        error_received = error
        return "recovered"

    async def failing_callback(**kwargs):
        raise ValueError("Test error")

    await framework.register("event", failing_callback, error_handler=error_handler)

    result = await framework.trigger_chain("event")
    assert result.action == ChainAction.CONTINUE
    assert isinstance(error_received, ValueError)
    assert str(error_received) == "Test error"
