# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Shared fixtures for callback framework tests.
"""

import pytest

from openjiuwen.core.runner.callback import (
    AsyncCallbackFramework,
    AuthFilter,
    CallbackChain,
    CallbackInfo,
    ChainContext,
    CircuitBreakerFilter,
    ConditionalFilter,
    LoggingFilter,
    ParamModifyFilter,
    RateLimitFilter,
    ValidationFilter,
)


@pytest.fixture
def framework():
    """Framework instance without metrics."""
    return AsyncCallbackFramework(enable_metrics=False, enable_logging=False)


@pytest.fixture
def framework_with_metrics():
    """Framework instance with metrics enabled."""
    return AsyncCallbackFramework(enable_metrics=True, enable_logging=False)


@pytest.fixture
def framework_with_logging():
    """Framework instance with logging enabled."""
    return AsyncCallbackFramework(enable_metrics=False, enable_logging=True)


@pytest.fixture
def rate_limit_filter():
    """RateLimitFilter with 3 calls per 2 seconds."""
    return RateLimitFilter(max_calls=3, time_window=2.0)


@pytest.fixture
def circuit_breaker_filter():
    """CircuitBreakerFilter with 3 failure threshold and 1s timeout."""
    return CircuitBreakerFilter(failure_threshold=3, timeout=1.0)


@pytest.fixture
def validation_filter():
    """ValidationFilter that validates value > 0."""
    return ValidationFilter(lambda value: value > 0)


@pytest.fixture
def logging_filter():
    """LoggingFilter instance."""
    return LoggingFilter()


@pytest.fixture
def auth_filter():
    """AuthFilter requiring 'admin' role."""
    return AuthFilter(required_role="admin")


@pytest.fixture
def param_modify_filter():
    """ParamModifyFilter that doubles the value."""

    def modifier(*args, **kwargs):
        value = kwargs.get('value', 0)
        return args, {'value': value * 2}

    return ParamModifyFilter(modifier)


@pytest.fixture
def conditional_filter():
    """ConditionalFilter that checks 'enabled' kwarg."""

    def condition(event, callback, *args, **kwargs):
        return kwargs.get('enabled', False)

    return ConditionalFilter(condition)


@pytest.fixture
def callback_chain():
    """Empty CallbackChain instance."""
    return CallbackChain(name="test_chain")


@pytest.fixture
def result_tracker():
    """List for tracking callback execution order."""
    return []


@pytest.fixture
def simple_async_callback():
    """Simple async callback function."""

    async def callback(message: str = "default"):
        return f"received: {message}"

    return callback


@pytest.fixture
def callback_info_factory():
    """Factory for creating CallbackInfo instances."""

    def factory(callback, priority=0, **kwargs):
        return CallbackInfo(
            callback=callback,
            priority=priority,
            **kwargs
        )

    return factory


@pytest.fixture
def chain_context_factory():
    """Factory for creating ChainContext instances."""

    def factory(event="test_event", args=(), kwargs=None):
        return ChainContext(
            event=event,
            initial_args=args,
            initial_kwargs=kwargs or {}
        )

    return factory
