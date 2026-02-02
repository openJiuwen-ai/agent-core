# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for callback framework data models.
"""

import math
import time

import pytest

from openjiuwen.core.runner.callback import (
    CallbackInfo,
    CallbackMetrics,
    ChainAction,
    ChainContext,
    ChainResult,
    FilterAction,
    FilterResult,
)


def test_callback_metrics_default_values():
    """Test CallbackMetrics default initialization."""
    metrics = CallbackMetrics()
    assert metrics.call_count == 0
    assert metrics.total_time == 0.0
    assert metrics.min_time == float('inf')
    assert metrics.max_time == 0.0
    assert metrics.error_count == 0
    assert metrics.last_call_time is None


def test_callback_metrics_update_success():
    """Test updating metrics with successful execution."""
    metrics = CallbackMetrics()
    metrics.update(0.5, is_error=False)

    assert metrics.call_count == 1
    assert metrics.total_time == 0.5
    assert metrics.min_time == 0.5
    assert metrics.max_time == 0.5
    assert metrics.error_count == 0
    assert metrics.last_call_time is not None


def test_callback_metrics_update_error():
    """Test updating metrics with error."""
    metrics = CallbackMetrics()
    metrics.update(0.3, is_error=True)

    assert metrics.call_count == 1
    assert metrics.error_count == 1


def test_callback_metrics_update_multiple_calls():
    """Test updating metrics with multiple calls."""
    metrics = CallbackMetrics()
    metrics.update(0.1)
    metrics.update(0.3)
    metrics.update(0.2)

    assert metrics.call_count == 3
    assert metrics.total_time == pytest.approx(0.6, abs=0.01)
    assert metrics.min_time == 0.1
    assert metrics.max_time == 0.3


def test_callback_metrics_avg_time_no_calls():
    """Test avg_time returns 0 when no calls."""
    metrics = CallbackMetrics()
    assert math.isclose(metrics.avg_time, 0.0)


def test_callback_metrics_avg_time_with_calls():
    """Test avg_time calculation."""
    metrics = CallbackMetrics()
    metrics.update(0.1)
    metrics.update(0.3)
    assert metrics.avg_time == pytest.approx(0.2, abs=0.01)


def test_callback_metrics_to_dict():
    """Test metrics conversion to dictionary."""
    metrics = CallbackMetrics()
    metrics.update(0.5)
    metrics.update(0.3, is_error=True)

    result = metrics.to_dict()

    assert result["call_count"] == 2
    assert result["avg_time"] == pytest.approx(0.4, abs=0.01)
    assert result["min_time"] == 0.3
    assert result["max_time"] == 0.5
    assert result["error_count"] == 1
    assert result["error_rate"] == 0.5
    assert result["last_call_time"] is not None


def test_callback_metrics_to_dict_no_calls():
    """Test to_dict when no calls have been made."""
    metrics = CallbackMetrics()
    result = metrics.to_dict()

    assert result["call_count"] == 0
    assert result["min_time"] == 0  # Should convert inf to 0
    assert result["error_rate"] == 0


def test_filter_result_continue():
    """Test FilterResult with CONTINUE action."""
    result = FilterResult(action=FilterAction.CONTINUE)
    assert result.action == FilterAction.CONTINUE
    assert result.modified_args is None
    assert result.modified_kwargs is None
    assert result.reason is None


def test_filter_result_skip_with_reason():
    """Test FilterResult with SKIP action and reason."""
    result = FilterResult(
        action=FilterAction.SKIP,
        reason="Rate limit exceeded"
    )
    assert result.action == FilterAction.SKIP
    assert result.reason == "Rate limit exceeded"


def test_filter_result_modify():
    """Test FilterResult with MODIFY action and modified args."""
    result = FilterResult(
        action=FilterAction.MODIFY,
        modified_args=(1, 2, 3),
        modified_kwargs={"key": "value"}
    )
    assert result.action == FilterAction.MODIFY
    assert result.modified_args == (1, 2, 3)
    assert result.modified_kwargs == {"key": "value"}


def test_chain_context_default_initialization():
    """Test ChainContext default values."""
    context = ChainContext(
        event="test_event",
        initial_args=("arg1",),
        initial_kwargs={"key": "value"}
    )

    assert context.event == "test_event"
    assert context.initial_args == ("arg1",)
    assert context.initial_kwargs == {"key": "value"}
    assert context.results == []
    assert context.metadata == {}
    assert context.current_index == 0
    assert context.is_completed is False
    assert context.is_rolled_back is False
    assert context.start_time > 0


def test_chain_context_get_last_result_empty():
    """Test get_last_result with no results."""
    context = ChainContext(
        event="test",
        initial_args=(),
        initial_kwargs={}
    )
    assert context.get_last_result() is None


def test_chain_context_get_last_result():
    """Test get_last_result with results."""
    context = ChainContext(
        event="test",
        initial_args=(),
        initial_kwargs={}
    )
    context.results = ["first", "second", "third"]
    assert context.get_last_result() == "third"


def test_chain_context_get_all_results():
    """Test get_all_results returns a copy."""
    context = ChainContext(
        event="test",
        initial_args=(),
        initial_kwargs={}
    )
    context.results = ["a", "b"]
    results = context.get_all_results()

    assert results == ["a", "b"]
    # Verify it's a copy
    results.append("c")
    assert len(context.results) == 2


def test_chain_context_metadata_operations():
    """Test metadata set and get operations."""
    context = ChainContext(
        event="test",
        initial_args=(),
        initial_kwargs={}
    )

    context.set_metadata("key1", "value1")
    assert context.get_metadata("key1") == "value1"
    assert context.get_metadata("nonexistent") is None
    assert context.get_metadata("nonexistent", "default") == "default"


def test_chain_context_elapsed_time():
    """Test elapsed_time property."""
    context = ChainContext(
        event="test",
        initial_args=(),
        initial_kwargs={}
    )

    time.sleep(0.05)
    elapsed = context.elapsed_time

    assert elapsed >= 0.05


def test_chain_result_continue():
    """Test ChainResult with CONTINUE action."""
    result = ChainResult(action=ChainAction.CONTINUE, result="success")
    assert result.action == ChainAction.CONTINUE
    assert result.result == "success"
    assert result.context is None
    assert result.error is None


def test_chain_result_rollback_with_error():
    """Test ChainResult with ROLLBACK action and error."""
    error = ValueError("Something went wrong")
    context = ChainContext(
        event="test",
        initial_args=(),
        initial_kwargs={}
    )
    result = ChainResult(
        action=ChainAction.ROLLBACK,
        context=context,
        error=error
    )

    assert result.action == ChainAction.ROLLBACK
    assert result.context is context
    assert result.error is error


def test_chain_result_break():
    """Test ChainResult with BREAK action."""
    result = ChainResult(action=ChainAction.BREAK, result={"data": "value"})
    assert result.action == ChainAction.BREAK
    assert result.result == {"data": "value"}


def test_callback_info_default_initialization():
    """Test CallbackInfo with default values."""

    async def dummy_callback():
        pass

    info = CallbackInfo(callback=dummy_callback, priority=0)

    assert info.callback is dummy_callback
    assert info.priority == 0
    assert info.once is False
    assert info.enabled is True
    assert info.namespace == "default"
    assert info.tags == set()
    assert info.max_retries == 0
    assert info.retry_delay == 0.0
    assert info.timeout is None
    assert info.created_at > 0


def test_callback_info_full_initialization():
    """Test CallbackInfo with all parameters."""

    async def dummy_callback():
        pass

    info = CallbackInfo(
        callback=dummy_callback,
        priority=10,
        once=True,
        enabled=False,
        namespace="custom",
        tags={"tag1", "tag2"},
        max_retries=3,
        retry_delay=1.0,
        timeout=30.0
    )

    assert info.priority == 10
    assert info.once is True
    assert info.enabled is False
    assert info.namespace == "custom"
    assert info.tags == {"tag1", "tag2"}
    assert info.max_retries == 3
    assert info.retry_delay == 1.0
    assert info.timeout == 30.0


def test_callback_info_hash():
    """Test CallbackInfo hash is based on callback identity."""

    async def callback1():
        pass

    async def callback2():
        pass

    info1 = CallbackInfo(callback=callback1, priority=0)
    info2 = CallbackInfo(callback=callback1, priority=10)
    info3 = CallbackInfo(callback=callback2, priority=0)

    # Same callback should have same hash (based on id)
    assert hash(info1) == hash(info2)
    # Different callbacks should have different hashes
    assert hash(info1) != hash(info3)
