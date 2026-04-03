# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Tests for callback framework registration and decorators.
"""
import pytest

from openjiuwen.core.runner.callback import (
    ChainAction,
    ChainResult,
)


@pytest.mark.asyncio
async def test_register_basic(framework):
    """Test basic callback registration."""

    async def callback(message: str):
        return f"received: {message}"

    await framework.register("test_event", callback)
    callbacks = framework.list_callbacks("test_event")
    assert len(callbacks) == 1
    assert callbacks[0]["name"] == "callback"


@pytest.mark.asyncio
async def test_register_with_priority(framework):
    """Test registration with priority."""

    async def low():
        pass

    async def high():
        pass

    await framework.register("event", low, priority=1)
    await framework.register("event", high, priority=10)
    callbacks = framework.list_callbacks("event")
    assert callbacks[0]["name"] == "high"
    assert callbacks[1]["name"] == "low"


@pytest.mark.asyncio
async def test_register_with_namespace(framework):
    """Test registration with namespace."""

    async def callback():
        pass

    await framework.register("event", callback, namespace="custom")
    callbacks = framework.list_callbacks("event")
    assert callbacks[0]["namespace"] == "custom"


@pytest.mark.asyncio
async def test_register_with_tags(framework):
    """Test registration with tags."""

    async def callback():
        pass

    await framework.register("event", callback, tags={"tag1", "tag2"})
    callbacks = framework.list_callbacks("event")
    assert set(callbacks[0]["tags"]) == {"tag1", "tag2"}


@pytest.mark.asyncio
async def test_register_with_once(framework):
    """Test registration with once flag."""

    async def callback():
        pass

    await framework.register("event", callback, once=True)
    callbacks = framework.list_callbacks("event")
    assert callbacks[0]["once"] is True


@pytest.mark.asyncio
async def test_register_with_retry_settings(framework):
    """Test registration with retry settings."""

    async def callback():
        pass

    await framework.register(
        "event",
        callback,
        max_retries=3,
        retry_delay=1.0,
        timeout=30.0
    )
    callbacks = framework.list_callbacks("event")
    assert callbacks[0]["max_retries"] == 3
    assert callbacks[0]["timeout"] == 30.0


@pytest.mark.asyncio
async def test_unregister_callback(framework):
    """Test unregistering a specific callback."""

    async def callback1():
        pass

    async def callback2():
        pass

    await framework.register("event", callback1)
    await framework.register("event", callback2)
    assert len(framework.list_callbacks("event")) == 2
    await framework.unregister("event", callback1)
    callbacks = framework.list_callbacks("event")
    assert len(callbacks) == 1
    assert callbacks[0]["name"] == "callback2"


@pytest.mark.asyncio
async def test_unregister_nonexistent_callback(framework):
    """Test unregistering nonexistent callback does not error."""

    async def callback():
        pass

    async def other():
        pass

    await framework.register("event", callback)
    # Should not raise
    await framework.unregister("event", other)
    assert len(framework.list_callbacks("event")) == 1


@pytest.mark.asyncio
async def test_unregister_namespace(framework):
    """Test unregistering all callbacks in a namespace."""

    async def cb1():
        pass

    async def cb2():
        pass

    async def cb3():
        pass

    await framework.register("event", cb1, namespace="ns1")
    await framework.register("event", cb2, namespace="ns1")
    await framework.register("event", cb3, namespace="ns2")
    await framework.unregister_namespace("ns1")
    callbacks = framework.list_callbacks("event")
    assert len(callbacks) == 1
    assert callbacks[0]["namespace"] == "ns2"


@pytest.mark.asyncio
async def test_unregister_by_tags(framework):
    """Test unregistering callbacks by tags."""

    async def cb1():
        pass

    async def cb2():
        pass

    async def cb3():
        pass

    await framework.register("event", cb1, tags={"debug"})
    await framework.register("event", cb2, tags={"debug", "verbose"})
    await framework.register("event", cb3, tags={"production"})
    await framework.unregister_by_tags({"debug"})
    callbacks = framework.list_callbacks("event")
    assert len(callbacks) == 1
    assert "production" in callbacks[0]["tags"]


def test_on_decorator_basic(framework):
    """Test @on decorator registers callback."""

    @framework.on("test_event")
    async def handler(message: str):
        return f"got: {message}"

    callbacks = framework.list_callbacks("test_event")
    assert len(callbacks) == 1
    assert callbacks[0]["name"] == "handler"


def test_on_decorator_with_priority(framework):
    """Test @on decorator with priority."""

    @framework.on("event", priority=10)
    async def high_priority():
        pass

    @framework.on("event", priority=1)
    async def low_priority():
        pass

    callbacks = framework.list_callbacks("event")
    assert callbacks[0]["priority"] == 10
    assert callbacks[1]["priority"] == 1


def test_on_decorator_with_namespace_and_tags(framework):
    """Test @on decorator with namespace and tags."""

    @framework.on("event", namespace="custom", tags={"tag1"})
    async def handler():
        pass

    callbacks = framework.list_callbacks("event")
    assert callbacks[0]["namespace"] == "custom"
    assert "tag1" in callbacks[0]["tags"]


def test_on_decorator_preserves_function(framework):
    """Test @on decorator preserves original function."""

    @framework.on("event")
    async def my_handler(x: int) -> int:
        """My docstring."""
        return x * 2

    assert my_handler.__name__ == "my_handler"
    assert my_handler.__doc__ == "My docstring."


@pytest.mark.asyncio
async def test_emit_before_basic(framework):
    """Test @emit_before triggers event before function."""
    call_log = []

    @framework.on("processing")
    async def on_processing(data):
        call_log.append(f"event: {data}")

    @framework.emit_before("processing")
    async def process(data):
        call_log.append(f"process: {data}")
        return {"processed": data}

    result = await process("test")
    assert call_log == ["event: test", "process: test"]
    assert result == {"processed": "test"}


@pytest.mark.asyncio
async def test_emit_before_pass_args_false(framework):
    """Test @emit_before with pass_args=False."""
    received_args = []

    @framework.on("event")
    async def handler(*args, **kwargs):
        received_args.append((args, kwargs))

    @framework.emit_before("event", pass_args=False)
    async def my_func(data):
        return data

    await my_func("secret_data")
    assert received_args[0] == ((), {'session': None})


@pytest.mark.asyncio
async def test_emit_after_basic(framework):
    """Test @emit_after triggers event with result."""
    received_results = []

    @framework.on("data_ready")
    async def on_ready(result):
        received_results.append(result)

    @framework.emit_after("data_ready")
    async def process():
        return {"status": "done"}

    result = await process()
    assert result == {"status": "done"}
    assert received_results == [{"status": "done"}]


@pytest.mark.asyncio
async def test_emit_after_custom_result_key(framework):
    """Test @emit_after with custom result_key."""
    received = []

    @framework.on("event")
    async def handler(**kwargs):
        received.append(kwargs)

    @framework.emit_after("event", result_key="data")
    async def process():
        return {"value": 42}

    await process()
    assert received[0]["data"] == {"value": 42}


@pytest.mark.asyncio
async def test_emit_after_pass_args(framework):
    """Test @emit_after with pass_args=True."""
    received = []

    @framework.on("event")
    async def handler(*args, **kwargs):
        received.append({"args": args, "kwargs": kwargs})

    @framework.emit_after("event", pass_args=True)
    async def process(input_data, extra="default"):
        return "result"

    await process("my_input", extra="custom")
    assert received[0]["args"] == ("my_input",)
    assert received[0]["kwargs"]["extra"] == "custom"
    assert received[0]["kwargs"]["result"] == "result"


@pytest.mark.asyncio
async def test_emit_around_basic(framework):
    """Test @emit_around triggers before and after events."""
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
async def test_emit_around_with_error_event(framework):
    """Test @emit_around triggers error event on exception."""
    error_log = []

    @framework.on("start")
    async def on_start():
        error_log.append("started")

    @framework.on("error")
    async def on_error(error):
        error_log.append(f"error: {type(error).__name__}")

    @framework.emit_around("start", "end", on_error_event="error")
    async def failing_process():
        raise ValueError("Something went wrong")

    with pytest.raises(ValueError):
        await failing_process()
    assert "started" in error_log
    assert "error: ValueError" in error_log


@pytest.mark.asyncio
async def test_emit_around_pass_args(framework):
    """Test @emit_around passes arguments to events."""
    received = []

    @framework.on("before")
    async def on_before(x, y):
        received.append(f"before: {x}, {y}")

    @framework.on("after")
    async def on_after(x, y, result):
        received.append(f"after: {x}, {y}, result={result}")

    @framework.emit_around("before", "after", pass_args=True, pass_result=True)
    async def add(x, y):
        return x + y

    result = await add(3, 4)
    assert result == 7
    assert "before: 3, 4" in received
    assert "after: 3, 4, result=7" in received


@pytest.mark.asyncio
async def test_emit_around_pass_args_false_before(framework):
    """Test @emit_around with pass_args=False for before event."""
    received_before = []
    received_after = []

    @framework.on("before")
    async def on_before(*args, **kwargs):
        received_before.append((args, kwargs))

    @framework.on("after")
    async def on_after(**kwargs):
        received_after.append(kwargs)

    @framework.emit_around("before", "after", pass_args=False, pass_result=True)
    async def my_func(data):
        return {"processed": data}

    await my_func("secret")
    # Before should have no args
    assert received_before[0] == ((), {'session': None})
    # After should have result only
    assert "result" in received_after[0]


@pytest.mark.asyncio
async def test_emit_around_pass_result_false(framework):
    """Test @emit_around with pass_result=False."""
    received_after = []

    @framework.on("before")
    async def on_before():
        pass

    @framework.on("after")
    async def on_after(*args, **kwargs):
        received_after.append((args, kwargs))

    @framework.emit_around("before", "after", pass_args=False, pass_result=False)
    async def my_func():
        return {"status": "done"}

    result = await my_func()
    assert result == {"status": "done"}
    # After event should have no args since pass_args and pass_result are False
    assert received_after[0] == ((), {'session': None})


@pytest.mark.asyncio
async def test_emit_around_pass_result_false_with_args(framework):
    """Test @emit_around with pass_result=False but pass_args=True."""
    received_after = []

    @framework.on("before")
    async def on_before(x):
        pass

    @framework.on("after")
    async def on_after(x, **kwargs):
        received_after.append({"x": x, "kwargs": kwargs})

    @framework.emit_around("before", "after", pass_args=True, pass_result=False)
    async def my_func(x):
        return x * 2

    result = await my_func(5)
    assert result == 10
    # After should have args but no result
    assert received_after[0]["x"] == 5
    assert "result" not in received_after[0]["kwargs"]


@pytest.mark.asyncio
async def test_emit_around_error_pass_args_false(framework):
    """Test @emit_around error event with pass_args=False."""
    received_error = []

    @framework.on("start")
    async def on_start():
        pass

    @framework.on("error")
    async def on_error(**kwargs):
        received_error.append(kwargs)

    @framework.emit_around("start", "end", pass_args=False, on_error_event="error")
    async def failing_func(secret_data):
        raise ValueError("Failed!")

    with pytest.raises(ValueError):
        await failing_func("secret")
    # Error event should have error but no args
    assert "error" in received_error[0]
    assert isinstance(received_error[0]["error"], ValueError)


@pytest.mark.asyncio
async def test_on_decorator_wrapper_called(framework):
    """Test the wrapper function in @on decorator is called."""

    @framework.on("event")
    async def my_callback(x: int):
        return x * 2

    # Call the decorated function directly
    result = await my_callback(5)
    assert result == 10


@pytest.mark.asyncio
async def test_register_with_callback_filters(framework):
    """Test async register with callback-specific filters."""
    from openjiuwen.core.runner.callback import ValidationFilter

    validator = ValidationFilter(lambda x: x > 0)

    async def callback(x):
        return x

    await framework.register("event", callback, filters=[validator])
    # Valid call should work
    results = await framework.trigger("event", 10)
    assert results == [10]
    # Invalid call should be skipped
    results = await framework.trigger("event", -5)
    assert results == []


def test_sync_register_with_filters(framework):
    """Test sync register (_register_sync) with filters."""
    from openjiuwen.core.runner.callback import ValidationFilter

    validator = ValidationFilter(lambda x: x > 0)

    async def callback(x):
        return x

    framework.register_sync("event", callback, filters=[validator])
    assert callback in framework.callback_filters


@pytest.mark.asyncio
async def test_register_logs_message(framework_with_logging, caplog):
    """Test registration logs a message."""
    import logging

    async def callback():
        pass

    with caplog.at_level(logging.INFO):
        await framework_with_logging.register("event", callback)
    assert any("Registered callback" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_unregister_logs_message(framework_with_logging, caplog):
    """Test unregistration logs a message."""
    import logging

    async def callback():
        pass

    await framework_with_logging.register("event", callback)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        await framework_with_logging.unregister("event", callback)
    assert any("Unregistered callback" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_unregister_removes_from_chain(framework):
    """Test unregistering callback also removes from chain."""

    async def callback(**kwargs):
        return "result"

    async def rollback(context):
        pass

    await framework.register(
        "event",
        callback,
        rollback_handler=rollback
    )
    assert "event" in framework.chains
    await framework.unregister("event", callback)
    # Chain should have the callback removed
    chain = framework.chains.get("event")
    if chain:
        assert callback not in [ci.callback for ci in chain.callbacks]


@pytest.mark.asyncio
async def test_register_with_rollback_handler(framework):
    """Test registering callback with rollback handler."""
    rollback_called = False

    async def my_callback(**kwargs):
        return ChainResult(ChainAction.ROLLBACK, error=Exception("fail"))

    async def my_rollback(context):
        nonlocal rollback_called
        rollback_called = True

    await framework.register(
        "chain_event",
        my_callback,
        rollback_handler=my_rollback
    )
    result = await framework.trigger_chain("chain_event")
    # Rollback should have been set up through chain
    assert result.action == ChainAction.ROLLBACK


@pytest.mark.asyncio
async def test_on_decorator_with_handlers(framework):
    """Test @on decorator with rollback and error handlers."""
    handler_calls = []

    async def error_handler(error, context):
        handler_calls.append(f"error: {error}")
        return "recovered"

    async def rollback_handler(context):
        handler_calls.append("rollback")

    @framework.on(
        "event",
        error_handler=error_handler,
        rollback_handler=rollback_handler
    )
    async def my_callback(**kwargs):
        raise ValueError("Test error")

    # Error handler should recover
    result = await framework.trigger_chain("event")
    assert result.action == ChainAction.CONTINUE
    assert "error: Test error" in handler_calls
