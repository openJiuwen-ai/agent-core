# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for callback framework filters.
"""

import asyncio
import logging

import pytest

from openjiuwen.core.runner.callback import (
    AuthFilter,
    CircuitBreakerFilter,
    ConditionalFilter,
    EventFilter,
    FilterAction,
    LoggingFilter,
    ParamModifyFilter,
    RateLimitFilter,
    ValidationFilter,
)


@pytest.mark.asyncio
async def test_event_filter_default_continues():
    """Test base EventFilter returns CONTINUE by default."""
    filter_obj = EventFilter()

    async def dummy_callback():
        pass

    result = await filter_obj.filter("test_event", dummy_callback)

    assert result.action == FilterAction.CONTINUE


def test_event_filter_default_name():
    """Test filter uses class name as default name."""
    filter_obj = EventFilter()
    assert filter_obj.name == "EventFilter"


def test_event_filter_custom_name():
    """Test filter can have custom name."""
    filter_obj = EventFilter(name="CustomFilter")
    assert filter_obj.name == "CustomFilter"


@pytest.mark.asyncio
async def test_rate_limit_filter_allows_within_limit():
    """Test filter allows calls within rate limit."""
    filter_obj = RateLimitFilter(max_calls=3, time_window=2.0)

    async def callback():
        pass

    for i in range(3):
        result = await filter_obj.filter("test", callback)
        assert result.action == FilterAction.CONTINUE, f"Call {i + 1} should be allowed"


@pytest.mark.asyncio
async def test_rate_limit_filter_blocks_exceeding_limit():
    """Test filter blocks calls exceeding rate limit."""
    filter_obj = RateLimitFilter(max_calls=2, time_window=2.0)

    async def callback():
        pass

    # First two calls should pass
    await filter_obj.filter("test", callback)
    await filter_obj.filter("test", callback)

    # Third call should be blocked
    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.SKIP
    assert "Rate limit exceeded" in result.reason


@pytest.mark.asyncio
async def test_rate_limit_filter_different_callbacks_tracked_separately():
    """Test rate limit is tracked per callback."""
    filter_obj = RateLimitFilter(max_calls=2, time_window=2.0)

    async def callback1():
        pass

    async def callback2():
        pass

    # Both callbacks can use their own limits
    for _ in range(2):
        result = await filter_obj.filter("test", callback1)
        assert result.action == FilterAction.CONTINUE

    for _ in range(2):
        result = await filter_obj.filter("test", callback2)
        assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_rate_limit_filter_window_expiration():
    """Test rate limit resets after time window."""
    filter_obj = RateLimitFilter(max_calls=2, time_window=0.1)

    async def callback():
        pass

    # Exhaust limit
    await filter_obj.filter("test", callback)
    await filter_obj.filter("test", callback)

    # Wait for window to expire
    await asyncio.sleep(0.15)

    # Should be allowed again
    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.CONTINUE


def test_rate_limit_filter_custom_name():
    """Test RateLimitFilter can have custom name."""
    filter_obj = RateLimitFilter(max_calls=10, time_window=1.0, name="CustomRateLimit")
    assert filter_obj.name == "CustomRateLimit"


@pytest.mark.asyncio
async def test_circuit_breaker_filter_closed_state_allows_calls():
    """Test filter allows calls when circuit is closed."""
    filter_obj = CircuitBreakerFilter(failure_threshold=3, timeout=1.0)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_circuit_breaker_filter_opens_after_threshold():
    """Test circuit opens after failure threshold."""
    filter_obj = CircuitBreakerFilter(failure_threshold=3, timeout=1.0)

    async def callback():
        pass

    # Record failures
    for _ in range(3):
        await filter_obj.record_failure("test", callback)

    # Circuit should be open now
    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.SKIP
    assert "Circuit breaker open" in result.reason


@pytest.mark.asyncio
async def test_circuit_breaker_filter_success_resets_failures():
    """Test successful call resets failure count."""
    filter_obj = CircuitBreakerFilter(failure_threshold=3, timeout=1.0)

    async def callback():
        pass

    # Record some failures
    await filter_obj.record_failure("test", callback)
    await filter_obj.record_failure("test", callback)

    # Record success
    await filter_obj.record_success("test", callback)

    # Add more failures - shouldn't trip breaker yet
    await filter_obj.record_failure("test", callback)
    await filter_obj.record_failure("test", callback)

    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_circuit_breaker_filter_timeout_allows_retry():
    """Test circuit closes after timeout."""
    filter_obj = CircuitBreakerFilter(failure_threshold=2, timeout=0.1)

    async def callback():
        pass

    # Trip the breaker
    await filter_obj.record_failure("test", callback)
    await filter_obj.record_failure("test", callback)

    # Should be open
    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.SKIP

    # Wait for timeout
    await asyncio.sleep(0.15)

    # Should be allowed (half-open)
    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_circuit_breaker_filter_different_callbacks_tracked_separately():
    """Test circuit state is tracked per callback."""
    filter_obj = CircuitBreakerFilter(failure_threshold=2, timeout=1.0)

    async def callback1():
        pass

    async def callback2():
        pass

    # Trip breaker for callback1
    await filter_obj.record_failure("test", callback1)
    await filter_obj.record_failure("test", callback1)

    # callback1 should be blocked
    result = await filter_obj.filter("test", callback1)
    assert result.action == FilterAction.SKIP

    # callback2 should still work
    result = await filter_obj.filter("test", callback2)
    assert result.action == FilterAction.CONTINUE


def test_circuit_breaker_filter_custom_name():
    """Test CircuitBreakerFilter can have custom name."""
    filter_obj = CircuitBreakerFilter(name="CustomBreaker")
    assert filter_obj.name == "CustomBreaker"


@pytest.mark.asyncio
async def test_validation_filter_valid_args_continue():
    """Test filter continues with valid arguments."""
    filter_obj = ValidationFilter(lambda x: x > 0)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, 10)
    assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_validation_filter_invalid_args_skip():
    """Test filter skips with invalid arguments."""
    filter_obj = ValidationFilter(lambda x: x > 0)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, -5)
    assert result.action == FilterAction.SKIP
    assert "validation failed" in result.reason.lower()


@pytest.mark.asyncio
async def test_validation_filter_kwargs_validation():
    """Test filter can validate keyword arguments."""
    filter_obj = ValidationFilter(lambda **kwargs: kwargs.get('value', 0) > 0)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, value=10)
    assert result.action == FilterAction.CONTINUE

    result = await filter_obj.filter("test", callback, value=-5)
    assert result.action == FilterAction.SKIP


@pytest.mark.asyncio
async def test_validation_filter_validator_exception_skips():
    """Test filter skips when validator raises exception."""

    def bad_validator(*args):
        raise ValueError("Validation error")

    filter_obj = ValidationFilter(bad_validator)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, "arg")
    assert result.action == FilterAction.SKIP
    assert "Validation error" in result.reason


@pytest.mark.asyncio
async def test_logging_filter_always_continues():
    """Test LoggingFilter always returns CONTINUE."""
    filter_obj = LoggingFilter()

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, "arg1", key="value")
    assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_logging_filter_logs_execution_info(caplog):
    """Test LoggingFilter logs event information."""
    logger = logging.getLogger("test_logger")
    filter_obj = LoggingFilter(logger=logger)

    async def my_callback():
        pass

    with caplog.at_level(logging.INFO):
        await filter_obj.filter("test_event", my_callback, "arg1", key="value")

    assert "test_event" in caplog.text
    assert "my_callback" in caplog.text


def test_logging_filter_custom_logger():
    """Test LoggingFilter uses provided logger."""
    custom_logger = logging.getLogger("custom")
    filter_obj = LoggingFilter(logger=custom_logger)
    assert filter_obj.logger is custom_logger


def test_logging_filter_default_logger():
    """Test LoggingFilter creates default logger."""
    filter_obj = LoggingFilter()
    assert filter_obj.logger is not None


@pytest.mark.asyncio
async def test_auth_filter_authorized_user_continues():
    """Test filter continues for authorized user."""
    filter_obj = AuthFilter(required_role="admin")

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, user_role="admin")
    assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_auth_filter_unauthorized_user_skips():
    """Test filter skips for unauthorized user."""
    filter_obj = AuthFilter(required_role="admin")

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, user_role="guest")
    assert result.action == FilterAction.SKIP
    assert "Unauthorized" in result.reason
    assert "admin" in result.reason
    assert "guest" in result.reason


@pytest.mark.asyncio
async def test_auth_filter_missing_role_defaults_to_guest():
    """Test filter treats missing role as guest."""
    filter_obj = AuthFilter(required_role="admin")

    async def callback():
        pass

    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.SKIP


@pytest.mark.asyncio
async def test_param_modify_filter_modifies_arguments():
    """Test filter modifies arguments correctly."""

    def modifier(*args, **kwargs):
        new_kwargs = {k: v * 2 for k, v in kwargs.items() if isinstance(v, int)}
        return args, new_kwargs

    filter_obj = ParamModifyFilter(modifier)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, value=5)
    assert result.action == FilterAction.MODIFY
    assert result.modified_kwargs == {"value": 10}


@pytest.mark.asyncio
async def test_param_modify_filter_modifies_positional_args():
    """Test filter can modify positional arguments."""

    def modifier(*args, **kwargs):
        new_args = tuple(a * 2 if isinstance(a, int) else a for a in args)
        return new_args, kwargs

    filter_obj = ParamModifyFilter(modifier)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, 5, 10)
    assert result.action == FilterAction.MODIFY
    assert result.modified_args == (10, 20)


@pytest.mark.asyncio
async def test_param_modify_filter_modifier_exception_skips():
    """Test filter skips when modifier raises exception."""

    def bad_modifier(*args, **kwargs):
        raise RuntimeError("Modifier failed")

    filter_obj = ParamModifyFilter(bad_modifier)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback, value=5)
    assert result.action == FilterAction.SKIP
    assert "modification failed" in result.reason.lower()


@pytest.mark.asyncio
async def test_conditional_filter_condition_true_continues():
    """Test filter continues when condition is true."""

    def condition(event, callback, *args, **kwargs):
        return True

    filter_obj = ConditionalFilter(condition)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.CONTINUE


@pytest.mark.asyncio
async def test_conditional_filter_condition_false_skips():
    """Test filter skips when condition is false."""

    def condition(event, callback, *args, **kwargs):
        return False

    filter_obj = ConditionalFilter(condition)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.SKIP
    assert "Condition not satisfied" in result.reason


@pytest.mark.asyncio
async def test_conditional_filter_condition_uses_event_and_args():
    """Test condition receives event and arguments."""
    received = {}

    def condition(event, callback, *args, **kwargs):
        received["event"] = event
        received["callback"] = callback
        received["args"] = args
        received["kwargs"] = kwargs
        return True

    filter_obj = ConditionalFilter(condition)

    async def my_callback():
        pass

    await filter_obj.filter("my_event", my_callback, "arg1", key="value")

    assert received["event"] == "my_event"
    assert received["callback"] is my_callback
    assert received["args"] == ("arg1",)
    assert received["kwargs"] == {"key": "value"}


@pytest.mark.asyncio
async def test_conditional_filter_custom_action_on_false():
    """Test filter can use custom action when condition is false."""

    def condition(event, callback, *args, **kwargs):
        return False

    filter_obj = ConditionalFilter(condition, action_on_false=FilterAction.STOP)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.STOP


@pytest.mark.asyncio
async def test_conditional_filter_condition_exception_skips():
    """Test filter skips when condition raises exception."""

    def bad_condition(*args, **kwargs):
        raise RuntimeError("Condition failed")

    filter_obj = ConditionalFilter(bad_condition)

    async def callback():
        pass

    result = await filter_obj.filter("test", callback)
    assert result.action == FilterAction.SKIP
    assert "evaluation failed" in result.reason.lower()
