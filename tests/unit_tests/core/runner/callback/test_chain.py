# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for CallbackChain execution.
"""

import asyncio

import pytest

from openjiuwen.core.runner.callback import (
    CallbackChain,
    CallbackInfo,
    ChainAction,
    ChainContext,
    ChainResult,
)


def test_initialization():
    """Test CallbackChain initialization."""
    chain = CallbackChain(name="test_chain")
    assert chain.name == "test_chain"
    assert chain.callbacks == []
    assert chain.rollback_handlers == {}
    assert chain.error_handlers == {}


def test_add_callback():
    """Test adding callback to chain."""
    chain = CallbackChain()

    async def callback():
        pass

    info = CallbackInfo(callback=callback, priority=10)
    chain.add(info)

    assert len(chain.callbacks) == 1
    assert chain.callbacks[0] is info


def test_add_multiple_callbacks_sorted_by_priority():
    """Test callbacks are sorted by priority (higher first)."""
    chain = CallbackChain()

    async def low_priority():
        pass

    async def high_priority():
        pass

    async def medium_priority():
        pass

    chain.add(CallbackInfo(callback=low_priority, priority=1))
    chain.add(CallbackInfo(callback=high_priority, priority=10))
    chain.add(CallbackInfo(callback=medium_priority, priority=5))

    assert chain.callbacks[0].priority == 10
    assert chain.callbacks[1].priority == 5
    assert chain.callbacks[2].priority == 1


def test_add_with_handlers():
    """Test adding callback with rollback and error handlers."""
    chain = CallbackChain()

    async def callback():
        pass

    async def rollback_handler(context):
        pass

    async def error_handler(error, context):
        pass

    info = CallbackInfo(callback=callback, priority=0)
    chain.add(info, rollback_handler=rollback_handler, error_handler=error_handler)

    assert chain.rollback_handlers[callback] is rollback_handler
    assert chain.error_handlers[callback] is error_handler


def test_remove_callback():
    """Test removing callback from chain."""
    chain = CallbackChain()

    async def callback1():
        pass

    async def callback2():
        pass

    chain.add(CallbackInfo(callback=callback1, priority=0))
    chain.add(CallbackInfo(callback=callback2, priority=0))

    assert len(chain.callbacks) == 2

    chain.remove(callback1)

    assert len(chain.callbacks) == 1
    assert chain.callbacks[0].callback is callback2


def test_remove_clears_handlers():
    """Test removing callback also clears its handlers."""
    chain = CallbackChain()

    async def callback():
        pass

    async def rollback(context):
        pass

    info = CallbackInfo(callback=callback, priority=0)
    chain.add(info, rollback_handler=rollback)

    assert callback in chain.rollback_handlers

    chain.remove(callback)

    assert callback not in chain.rollback_handlers


@pytest.mark.asyncio
async def test_execute_single_callback():
    """Test executing chain with single callback."""
    chain = CallbackChain()

    async def callback(**kwargs):
        return "result"

    chain.add(CallbackInfo(callback=callback, priority=0))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.CONTINUE
    assert result.result == "result"
    assert context.is_completed


@pytest.mark.asyncio
async def test_execute_multiple_callbacks():
    """Test executing chain passes results between callbacks."""
    chain = CallbackChain()
    execution_order = []

    async def step1(**kwargs):
        execution_order.append("step1")
        return {"step1": True}

    async def step2(prev_result, **kwargs):
        execution_order.append("step2")
        prev_result["step2"] = True
        return prev_result

    chain.add(CallbackInfo(callback=step1, priority=20))
    chain.add(CallbackInfo(callback=step2, priority=10))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert execution_order == ["step1", "step2"]
    assert result.result == {"step1": True, "step2": True}


@pytest.mark.asyncio
async def test_execute_respects_priority_order():
    """Test callbacks execute in priority order (high to low)."""
    chain = CallbackChain()
    order = []

    async def callback_a(*args, **kwargs):
        order.append("a")
        return ChainResult(ChainAction.CONTINUE, result="a")

    async def callback_b(*args, **kwargs):
        order.append("b")
        return ChainResult(ChainAction.CONTINUE, result="b")

    async def callback_c(*args, **kwargs):
        order.append("c")
        return ChainResult(ChainAction.CONTINUE, result="c")

    chain.add(CallbackInfo(callback=callback_b, priority=5))
    chain.add(CallbackInfo(callback=callback_a, priority=10))
    chain.add(CallbackInfo(callback=callback_c, priority=1))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    await chain.execute(context)

    assert order == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_execute_skips_disabled_callbacks():
    """Test disabled callbacks are skipped."""
    chain = CallbackChain()
    order = []

    async def enabled(**kwargs):
        order.append("enabled")

    async def disabled(**kwargs):
        order.append("disabled")

    chain.add(CallbackInfo(callback=enabled, priority=10, enabled=True))
    chain.add(CallbackInfo(callback=disabled, priority=5, enabled=False))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    await chain.execute(context)

    assert order == ["enabled"]


@pytest.mark.asyncio
async def test_execute_chain_context_available():
    """Test _chain_context is passed to callbacks."""
    chain = CallbackChain()
    received_context = None

    async def callback(**kwargs):
        nonlocal received_context
        received_context = kwargs.get("_chain_context")
        return "done"

    chain.add(CallbackInfo(callback=callback, priority=0))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    await chain.execute(context)

    assert received_context is context


@pytest.mark.asyncio
async def test_break_action_stops_chain():
    """Test BREAK action stops chain execution."""
    chain = CallbackChain()
    executed = []

    async def step1(**kwargs):
        executed.append("step1")
        return ChainResult(ChainAction.BREAK, result="stopped_here")

    async def step2(**kwargs):
        executed.append("step2")
        return "step2_result"

    chain.add(CallbackInfo(callback=step1, priority=10))
    chain.add(CallbackInfo(callback=step2, priority=5))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.BREAK
    assert result.result == "stopped_here"
    assert executed == ["step1"]


@pytest.mark.asyncio
async def test_rollback_action_triggers_rollback():
    """Test ROLLBACK action triggers rollback handlers."""
    chain = CallbackChain()
    rollback_order = []

    async def step1(**kwargs):
        return ChainResult(ChainAction.CONTINUE, result="step1")

    async def step2(**kwargs):
        return ChainResult(ChainAction.ROLLBACK, error=Exception("Failed"))

    async def rollback1(context):
        rollback_order.append("rollback1")

    async def rollback2(context):
        rollback_order.append("rollback2")

    chain.add(
        CallbackInfo(callback=step1, priority=10),
        rollback_handler=rollback1
    )
    chain.add(
        CallbackInfo(callback=step2, priority=5),
        rollback_handler=rollback2
    )

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.ROLLBACK
    assert context.is_rolled_back
    # Only step1 was successfully executed, so only its rollback runs
    assert "rollback1" in rollback_order


@pytest.mark.asyncio
async def test_retry_action_retries_callback():
    """Test RETRY action causes callback retry."""
    chain = CallbackChain()
    call_count = 0

    async def flaky_callback(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return ChainResult(ChainAction.RETRY)
        return ChainResult(ChainAction.CONTINUE, result="success")

    chain.add(CallbackInfo(callback=flaky_callback, priority=0, max_retries=5))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.CONTINUE
    assert result.result == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_exception_triggers_rollback():
    """Test exception in callback triggers rollback."""
    chain = CallbackChain()
    rollback_called = False

    async def step1(*args, **kwargs):
        return "step1_result"

    async def failing_step(*args, **kwargs):
        raise RuntimeError("Something went wrong")

    async def rollback1(context):
        nonlocal rollback_called
        rollback_called = True

    chain.add(
        CallbackInfo(callback=step1, priority=10),
        rollback_handler=rollback1
    )
    chain.add(CallbackInfo(callback=failing_step, priority=5))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.ROLLBACK
    assert isinstance(result.error, RuntimeError)
    assert rollback_called


@pytest.mark.asyncio
async def test_error_handler_can_recover():
    """Test error handler can provide fallback result."""
    chain = CallbackChain()

    async def failing_callback(**kwargs):
        raise ValueError("Expected error")

    async def error_handler(error, context):
        return "recovered_result"

    chain.add(
        CallbackInfo(callback=failing_callback, priority=0),
        error_handler=error_handler
    )

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.CONTINUE
    assert result.result == "recovered_result"


@pytest.mark.asyncio
async def test_retry_on_exception():
    """Test callback with retries retries on exception."""
    chain = CallbackChain()
    attempts = 0

    async def flaky_callback(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("Temporary failure")
        return "success"

    chain.add(CallbackInfo(
        callback=flaky_callback,
        priority=0,
        max_retries=3,
        retry_delay=0.01
    ))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.CONTINUE
    assert result.result == "success"
    assert attempts == 3


@pytest.mark.asyncio
async def test_timeout_triggers_rollback():
    """Test timeout triggers rollback after retries exhausted."""
    chain = CallbackChain()

    async def slow_callback(**kwargs):
        await asyncio.sleep(1)
        return "never_reached"

    chain.add(CallbackInfo(
        callback=slow_callback,
        priority=0,
        timeout=0.05,
        max_retries=1
    ))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    assert result.action == ChainAction.ROLLBACK
    assert isinstance(result.error, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_error_handler_throws_exception():
    """Test chain continues when error handler itself throws."""
    chain = CallbackChain()

    async def failing_callback(*args, **kwargs):
        raise ValueError("Original error")

    async def failing_error_handler(error, context):
        raise RuntimeError("Error handler also failed!")

    chain.add(
        CallbackInfo(callback=failing_callback, priority=0),
        error_handler=failing_error_handler
    )

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    # Should rollback since error handler failed
    assert result.action == ChainAction.ROLLBACK
    assert isinstance(result.error, ValueError)


@pytest.mark.asyncio
async def test_rollback_handler_throws_exception():
    """Test chain continues when rollback handler throws."""
    chain = CallbackChain()
    step1_executed = False

    async def step1(*args, **kwargs):
        nonlocal step1_executed
        step1_executed = True
        return "step1"

    async def step2(*args, **kwargs):
        raise ValueError("Step 2 failed")

    async def failing_rollback(context):
        raise RuntimeError("Rollback failed!")

    chain.add(
        CallbackInfo(callback=step1, priority=10),
        rollback_handler=failing_rollback
    )
    chain.add(CallbackInfo(callback=step2, priority=5))

    context = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context)

    # Rollback should still complete even if handler fails
    assert result.action == ChainAction.ROLLBACK
    assert context.is_rolled_back
    assert step1_executed


@pytest.mark.asyncio
async def test_once_callback_disabled_after_execution():
    """Test once callback is disabled after first execution."""
    chain = CallbackChain()

    async def once_callback(**kwargs):
        return "executed"

    info = CallbackInfo(callback=once_callback, priority=0, once=True)
    chain.add(info)

    context1 = ChainContext(event="test", initial_args=(), initial_kwargs={})
    await chain.execute(context1)

    assert info.enabled is False

    # Second execution should skip the callback
    context2 = ChainContext(event="test", initial_args=(), initial_kwargs={})
    result = await chain.execute(context2)

    # Chain completes but with no results since callback is disabled
    assert result.action == ChainAction.CONTINUE
    assert len(context2.results) == 0
