# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for callback framework metrics, history, and persistence.
"""

import asyncio
import json
import os
import tempfile
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest


@pytest.mark.asyncio
async def test_metrics_enabled_collects_data(framework_with_metrics):
    """Test metrics are collected when enabled."""

    @framework_with_metrics.on("event")
    async def callback():
        await asyncio.sleep(0.01)
        return "done"

    await framework_with_metrics.trigger("event")

    metrics = framework_with_metrics.get_metrics()
    assert len(metrics) > 0
    assert "event:callback" in metrics


@pytest.mark.asyncio
async def test_metrics_disabled_no_collection(framework):
    """Test no metrics collected when disabled."""

    @framework.on("event")
    async def callback():
        return "done"

    await framework.trigger("event")

    metrics = framework.get_metrics()
    assert len(metrics) == 0


@pytest.mark.asyncio
async def test_metrics_tracks_call_count(framework_with_metrics):
    """Test metrics tracks call count correctly."""

    @framework_with_metrics.on("event")
    async def callback():
        return "done"

    for _ in range(5):
        await framework_with_metrics.trigger("event")

    metrics = framework_with_metrics.get_metrics("event", "callback")
    assert metrics["event:callback"]["call_count"] == 5


@pytest.mark.asyncio
async def test_metrics_tracks_timing(framework_with_metrics):
    """Test metrics tracks execution timing."""

    @framework_with_metrics.on("event")
    async def slow_callback():
        await asyncio.sleep(0.05)
        return "done"

    await framework_with_metrics.trigger("event")

    metrics = framework_with_metrics.get_metrics()
    metric = metrics["event:slow_callback"]

    assert metric["avg_time"] >= 0.05
    assert metric["min_time"] >= 0.05
    assert metric["max_time"] >= 0.05


@pytest.mark.asyncio
async def test_metrics_tracks_errors(framework_with_metrics):
    """Test metrics tracks error count."""

    @framework_with_metrics.on("event")
    async def failing_callback():
        raise ValueError("Error!")

    await framework_with_metrics.trigger("event")
    await framework_with_metrics.trigger("event")
    await framework_with_metrics.trigger("event")

    metrics = framework_with_metrics.get_metrics()
    metric = metrics["event:failing_callback"]

    assert metric["call_count"] == 3
    assert metric["error_count"] == 3
    assert metric["error_rate"] == 1.0


@pytest.mark.asyncio
async def test_get_metrics_filter_by_event(framework_with_metrics):
    """Test filtering metrics by event name."""

    @framework_with_metrics.on("event1")
    async def cb1():
        pass

    @framework_with_metrics.on("event2")
    async def cb2():
        pass

    await framework_with_metrics.trigger("event1")
    await framework_with_metrics.trigger("event2")

    metrics = framework_with_metrics.get_metrics(event="event1")

    assert len(metrics) == 1
    assert "event1:cb1" in metrics


@pytest.mark.asyncio
async def test_get_metrics_filter_by_callback(framework_with_metrics):
    """Test filtering metrics by callback name."""

    @framework_with_metrics.on("event")
    async def callback_a():
        pass

    @framework_with_metrics.on("event")
    async def callback_b():
        pass

    await framework_with_metrics.trigger("event")

    metrics = framework_with_metrics.get_metrics(callback="callback_a")

    assert len(metrics) == 1
    assert "event:callback_a" in metrics


@pytest.mark.asyncio
async def test_reset_metrics(framework_with_metrics):
    """Test resetting metrics clears all data."""

    @framework_with_metrics.on("event")
    async def callback():
        pass

    await framework_with_metrics.trigger("event")
    assert len(framework_with_metrics.get_metrics()) > 0

    framework_with_metrics.reset_metrics()

    assert len(framework_with_metrics.get_metrics()) == 0


@pytest.mark.asyncio
async def test_get_slow_callbacks(framework_with_metrics):
    """Test getting slow callbacks above threshold."""

    @framework_with_metrics.on("event")
    async def fast_callback():
        await asyncio.sleep(0.01)

    @framework_with_metrics.on("event")
    async def slow_callback():
        await asyncio.sleep(0.1)

    await framework_with_metrics.trigger("event")

    slow = framework_with_metrics.get_slow_callbacks(threshold=0.05)

    assert len(slow) == 1
    assert slow[0]["callback"] == "event:slow_callback"


@pytest.mark.asyncio
async def test_history_disabled_by_default(framework):
    """Test event history is disabled by default."""

    @framework.on("event")
    async def callback():
        pass

    await framework.trigger("event")

    history = framework.get_event_history()
    assert len(history) == 0


@pytest.mark.asyncio
async def test_enable_history(framework):
    """Test enabling event history."""
    framework.enable_event_history(True)

    @framework.on("event")
    async def callback():
        pass

    await framework.trigger("event", message="hello")

    history = framework.get_event_history()
    assert len(history) == 1
    assert history[0]["event"] == "event"
    assert history[0]["kwargs"]["message"] == "hello"


@pytest.mark.asyncio
async def test_history_filter_by_event(framework):
    """Test filtering history by event name."""
    framework.enable_event_history(True)

    @framework.on("event1")
    async def cb1():
        pass

    @framework.on("event2")
    async def cb2():
        pass

    await framework.trigger("event1")
    await framework.trigger("event2")
    await framework.trigger("event1")

    history = framework.get_event_history(event="event1")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_history_filter_by_since(framework):
    """Test filtering history by timestamp."""
    framework.enable_event_history(True)

    @framework.on("event")
    async def callback():
        pass

    await framework.trigger("event")
    await asyncio.sleep(0.1)

    since_time = datetime.now(tz=timezone.utc) - timedelta(milliseconds=50)

    await framework.trigger("event")

    history = framework.get_event_history(since=since_time)
    assert len(history) == 1


@pytest.mark.asyncio
async def test_replay_events(framework):
    """Test replaying recorded events."""
    framework.enable_event_history(True)

    call_count = 0

    @framework.on("event")
    async def callback(value):
        nonlocal call_count
        call_count += 1

    await framework.trigger("event", value=1)
    await framework.trigger("event", value=2)

    assert call_count == 2

    # Replay
    await framework.replay_events()

    assert call_count == 4


@pytest.mark.asyncio
async def test_save_state_creates_file(framework_with_metrics):
    """Test save_state creates a file."""

    @framework_with_metrics.on("event")
    async def callback():
        pass

    await framework_with_metrics.trigger("event")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        filepath = f.name

    try:
        framework_with_metrics.save_state(filepath)
        assert os.path.exists(filepath)
    finally:
        os.unlink(filepath)


@pytest.mark.asyncio
async def test_save_state_contains_metrics(framework_with_metrics):
    """Test saved state contains metrics."""

    @framework_with_metrics.on("event")
    async def callback():
        pass

    await framework_with_metrics.trigger("event")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        filepath = f.name

    try:
        framework_with_metrics.save_state(filepath)

        with open(filepath, 'r') as f:
            state = json.load(f)

        assert "metrics" in state
        assert "event:callback" in state["metrics"]
    finally:
        os.unlink(filepath)


@pytest.mark.asyncio
async def test_save_state_contains_callbacks(framework):
    """Test saved state contains callback metadata."""

    @framework.on("event", priority=10, namespace="custom", tags={"tag1"})
    async def my_callback():
        pass

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        filepath = f.name

    try:
        framework.save_state(filepath)

        with open(filepath, 'r') as f:
            state = json.load(f)

        assert "callbacks" in state
        assert "event" in state["callbacks"]
        callback_info = state["callbacks"]["event"][0]
        assert callback_info["name"] == "my_callback"
        assert callback_info["priority"] == 10
        assert callback_info["namespace"] == "custom"
        assert "tag1" in callback_info["tags"]
    finally:
        os.unlink(filepath)


@pytest.mark.asyncio
async def test_save_state_contains_history(framework):
    """Test saved state contains event history."""
    framework.enable_event_history(True)

    @framework.on("event")
    async def callback():
        pass

    await framework.trigger("event", value=42)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        filepath = f.name

    try:
        framework.save_state(filepath)

        with open(filepath, 'r') as f:
            state = json.load(f)

        assert "history" in state
        assert len(state["history"]) == 1
        assert state["history"][0]["event"] == "event"
    finally:
        os.unlink(filepath)


@pytest.mark.asyncio
async def test_get_statistics_basic(framework):
    """Test get_statistics returns expected keys."""
    stats = framework.get_statistics()

    assert "total_events" in stats
    assert "total_callbacks" in stats
    assert "namespaces" in stats
    assert "total_filters" in stats
    assert "total_chains" in stats
    assert "history_size" in stats
    assert "metrics_collected" in stats


@pytest.mark.asyncio
async def test_get_statistics_counts(framework):
    """Test statistics counts are accurate."""

    @framework.on("event1")
    async def cb1():
        pass

    @framework.on("event1")
    async def cb2():
        pass

    @framework.on("event2", namespace="custom")
    async def cb3():
        pass

    stats = framework.get_statistics()

    assert stats["total_events"] == 2
    assert stats["total_callbacks"] == 3
    assert "default" in stats["namespaces"]
    assert "custom" in stats["namespaces"]


@pytest.mark.asyncio
async def test_list_events(framework):
    """Test list_events returns all registered events."""

    @framework.on("event1")
    async def cb1():
        pass

    @framework.on("event2")
    async def cb2():
        pass

    events = framework.list_events()

    assert "event1" in events
    assert "event2" in events


@pytest.mark.asyncio
async def test_list_events_filter_by_namespace(framework):
    """Test list_events filters by namespace."""

    @framework.on("event1", namespace="ns1")
    async def cb1():
        pass

    @framework.on("event2", namespace="ns2")
    async def cb2():
        pass

    events = framework.list_events(namespace="ns1")

    assert events == ["event1"]


@pytest.mark.asyncio
async def test_list_callbacks(framework):
    """Test list_callbacks returns callback info."""

    @framework.on("event", priority=10, tags={"tag1"})
    async def my_callback():
        pass

    callbacks = framework.list_callbacks("event")

    assert len(callbacks) == 1
    assert callbacks[0]["name"] == "my_callback"
    assert callbacks[0]["priority"] == 10
    assert "tag1" in callbacks[0]["tags"]


@pytest.mark.asyncio
async def test_list_callbacks_empty_event(framework):
    """Test list_callbacks returns empty for nonexistent event."""
    callbacks = framework.list_callbacks("nonexistent")
    assert callbacks == []
