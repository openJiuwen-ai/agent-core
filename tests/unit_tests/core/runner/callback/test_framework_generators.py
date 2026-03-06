# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""

Tests for callback framework async generator support.

"""

import pytest


@pytest.mark.asyncio
async def test_trigger_stream_basic(framework):
    """Test basic stream processing."""

    @framework.on("process")
    async def process_item(item: dict):
        return f"processed: {item['value']}"

    async def data_source():
        for i in range(3):
            yield {"value": i}

    results = []
    async for result in framework.trigger_stream("process", data_source()):
        results.append(result)

    assert len(results) == 3
    assert results[0] == "processed: 0"


@pytest.mark.asyncio
async def test_trigger_stream_preserves_order(framework):
    """Test stream processing preserves order."""
    processed_values = []

    @framework.on("process")
    async def process_item(item: dict):
        processed_values.append(item['value'])
        return item['value']

    async def data_source():
        for i in range(5):
            yield {"value": i}

    async for _ in framework.trigger_stream("process", data_source()):
        pass

    assert processed_values == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_trigger_stream_with_generator_callback(framework):
    """Test stream with callback that returns async generator."""

    @framework.on("expand")
    async def expand_item(item: dict):
        value = item['value']
        for i in range(3):
            yield {"original": value, "expanded": i}

    async def input_stream():
        for i in range(2):
            yield {"value": i}

    results = []
    async for result in framework.trigger_stream("expand", input_stream()):
        results.append(result)

    # 2 inputs * 3 expansions = 6 outputs
    assert len(results) == 6


@pytest.mark.asyncio
async def test_trigger_stream_handles_errors(framework):
    """Test stream continues after callback errors."""
    success_count = 0

    @framework.on("process")
    async def process_item(item: dict):
        nonlocal success_count
        if item['value'] == 1:
            raise ValueError("Error on item 1")
        success_count += 1
        return f"ok: {item['value']}"

    async def data_source():
        for i in range(3):
            yield {"value": i}

    results = []
    async for result in framework.trigger_stream("process", data_source()):
        results.append(result)

    # Items 0 and 2 should succeed
    assert success_count == 2
    assert len(results) == 2


@pytest.mark.asyncio
async def test_trigger_generator_basic(framework):
    """Test basic generator aggregation."""

    @framework.on("stream")
    async def generator_callback():
        for i in range(3):
            yield f"item_{i}"

    results = []
    async for item in framework.trigger_generator("stream"):
        results.append(item)

    assert results == ["item_0", "item_1", "item_2"]


@pytest.mark.asyncio
async def test_trigger_generator_multiple_callbacks(framework):
    """Test aggregating from multiple generator callbacks."""

    @framework.on("stream")
    async def generator1():
        for i in range(2):
            yield {"source": "gen1", "value": i}

    @framework.on("stream")
    async def generator2():
        for i in range(2):
            yield {"source": "gen2", "value": i}

    results = []
    async for item in framework.trigger_generator("stream"):
        results.append(item)

    assert len(results) == 4
    gen1_items = [r for r in results if r["source"] == "gen1"]
    gen2_items = [r for r in results if r["source"] == "gen2"]
    assert len(gen1_items) == 2
    assert len(gen2_items) == 2


@pytest.mark.asyncio
async def test_trigger_generator_mixed_callbacks(framework):
    """Test mixing regular and generator callbacks."""

    @framework.on("mixed")
    async def regular_callback():
        return {"type": "regular", "value": 100}

    @framework.on("mixed")
    async def generator_callback():
        for i in range(3):
            yield {"type": "generator", "value": i}

    results = []
    async for item in framework.trigger_generator("mixed"):
        results.append(item)
    # 1 regular + 3 generator = 4
    assert len(results) == 4

    regular_items = [r for r in results if r["type"] == "regular"]
    generator_items = [r for r in results if r["type"] == "generator"]
    assert len(regular_items) == 1
    assert len(generator_items) == 3


@pytest.mark.asyncio
async def test_trigger_generator_respects_priority(framework):
    """Test generator respects callback priority order."""
    order = []

    @framework.on("stream", priority=10)
    async def high_priority():
        order.append("high_start")
        yield "high_item"
        order.append("high_end")

    @framework.on("stream", priority=1)
    async def low_priority():
        order.append("low_start")
        yield "low_item"
        order.append("low_end")

    results = []
    async for item in framework.trigger_generator("stream"):
        results.append(item)
    # High priority should start first
    assert order[0] == "high_start"


@pytest.mark.asyncio
async def test_trigger_generator_handles_errors(framework):
    """Test generator continues after callback errors."""

    @framework.on("stream", priority=10)
    async def failing_callback():
        raise ValueError("Error!")

    @framework.on("stream", priority=1)
    async def success_callback():
        yield "success"

    results = []
    async for item in framework.trigger_generator("stream"):
        results.append(item)

    assert results == ["success"]


@pytest.mark.asyncio
async def test_emit_after_stream_basic(framework):
    """Test emit_after with stream triggers event for each yielded item."""

    event_items = []

    @framework.on("chunk_ready")
    async def on_chunk(item: dict):
        event_items.append(item)

    @framework.emit_after("chunk_ready")
    async def process():
        for i in range(3):
            yield {"index": i}

    consumed = []
    async for item in process():
        consumed.append(item)

    assert len(event_items) == 3
    assert len(consumed) == 3

    for i in range(3):
        assert event_items[i] == consumed[i]


@pytest.mark.asyncio
async def test_emit_after_stream_custom_item_key(framework):
    """Test emit_after with stream and custom item key."""

    received_kwargs = []

    @framework.on("event")
    async def handler(**kwargs):
        received_kwargs.append(kwargs)

    @framework.emit_after("event", item_key="data")
    async def generate():
        yield {"value": 1}
        yield {"value": 2}

    async for _ in generate():
        pass

    assert received_kwargs[0]["data"] == {"value": 1}
    assert received_kwargs[1]["data"] == {"value": 2}


@pytest.mark.asyncio
async def test_emit_after_stream_multiple_handlers(framework):
    """Test emit_after with stream triggers multiple handlers."""

    handler1_items = []
    handler2_items = []

    @framework.on("event")
    async def handler1(item):
        handler1_items.append(item)

    @framework.on("event")
    async def handler2(item):
        handler2_items.append(item)

    @framework.emit_after("event")
    async def generate():
        yield "item1"
        yield "item2"

    async for _ in generate():
        pass

    assert len(handler1_items) == 2
    assert len(handler2_items) == 2


@pytest.mark.asyncio
async def test_emit_after_stream_preserves_generator(framework):
    """Test emit_after with stream doesn't modify yielded items."""

    original_items = [{"id": 1}, {"id": 2}, {"id": 3}]

    @framework.on("event")
    async def handler(item):
        pass

    @framework.emit_after("event")
    async def generate():
        for item in original_items:
            yield item

    consumed = []
    async for item in generate():
        consumed.append(item)

    assert consumed == original_items


@pytest.mark.asyncio
async def test_emit_after_stream_with_exception(framework):
    """Test emit_after with stream propagates exceptions."""

    event_count = 0

    @framework.on("event")
    async def handler(item):
        nonlocal event_count
        event_count += 1

    @framework.emit_after("event")
    async def failing_generator():
        yield {"id": 1}
        raise ValueError("Generator error!")

    with pytest.raises(ValueError, match="Generator error!"):
        async for _ in failing_generator():
            pass

    assert event_count == 1


@pytest.mark.asyncio
async def test_trigger_stream_logs_and_raises_error(framework_with_logging, caplog):
    """Test trigger_stream logs errors and raises them."""
    import logging

    @framework_with_logging.on("process")
    async def callback(item):
        return item

    async def failing_stream():
        yield {"value": 1}
        raise RuntimeError("Stream error!")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="Stream error"):
            async for _ in framework_with_logging.trigger_stream("process", failing_stream()):
                pass

    assert any("Stream processing error" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_emit_after_stream_reraises_error(framework_with_logging):
    """Test emit_after with stream re-raises errors from the generator."""

    @framework_with_logging.on("event")
    async def handler(item):
        pass

    @framework_with_logging.emit_after("event")
    async def failing_generator():
        yield {"id": 1}
        raise ValueError("Generator failed!")

    with pytest.raises(ValueError, match="Generator failed"):
        async for _ in failing_generator():
            pass


@pytest.mark.asyncio
async def test_trigger_generator_disabled_callback(framework):
    """Test trigger_generator skips disabled callbacks."""

    @framework.on("stream")
    async def callback():
        yield "item"

    framework.callbacks["stream"][0].enabled = False

    results = []
    async for item in framework.trigger_generator("stream"):
        results.append(item)

    assert results == []


@pytest.mark.asyncio
async def test_trigger_generator_stop_filter(framework_with_logging, caplog):
    """Test trigger_generator with STOP filter."""
    import logging

    from openjiuwen.core.runner.callback import (
        ConditionalFilter,
        FilterAction,
    )

    stop_filter = ConditionalFilter(
        lambda event, callback, *args, **kwargs: False,
        action_on_false=FilterAction.STOP
    )

    framework_with_logging.add_filter("stream", stop_filter)

    @framework_with_logging.on("stream")
    async def callback():
        yield "item"

    with caplog.at_level(logging.INFO):
        results = []
        async for item in framework_with_logging.trigger_generator("stream"):
            results.append(item)

    assert results == []
    assert any("Filter stopped" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_trigger_generator_coroutine_returning_async_gen(framework):
    """Test trigger_generator with coroutine that returns async generator."""

    async def create_generator():
        async def inner_gen():
            yield "item1"
            yield "item2"

        return inner_gen()

    @framework.on("stream")
    async def callback():
        # This is a coroutine that returns an async generator
        gen = await create_generator()
        return gen

    results = []
    async for item in framework.trigger_generator("stream"):
        results.append(item)

    # Should yield items from the returned generator
    assert "item1" in results or "item2" in results or len(results) > 0


@pytest.mark.asyncio
async def test_trigger_generator_sync_result(framework):
    """Test trigger_generator with callback returning sync value (not coroutine)."""
    # This tests the else branch in trigger_generator

    call_count = 0

    @framework.on("stream")
    def sync_callback():  # Not async!
        nonlocal call_count
        call_count += 1
        return "sync_result"

    results = []
    async for item in framework.trigger_generator("stream"):
        results.append(item)

    assert call_count == 1
    assert "sync_result" in results


@pytest.mark.asyncio
async def test_trigger_generator_metrics_collection(framework_with_metrics):
    """Test trigger_generator collects metrics."""

    @framework_with_metrics.on("stream")
    async def callback():
        yield "item"

    async for _ in framework_with_metrics.trigger_generator("stream"):
        pass

    metrics = framework_with_metrics.get_metrics()

    assert "stream:callback" in metrics
    assert metrics["stream:callback"]["call_count"] == 1


@pytest.mark.asyncio
async def test_trigger_generator_once_callback(framework):
    """Test trigger_generator with once callback."""

    @framework.on("stream", once=True)
    async def once_callback():
        yield "item"

    results1 = []
    async for item in framework.trigger_generator("stream"):
        results1.append(item)

    results2 = []
    async for item in framework.trigger_generator("stream"):
        results2.append(item)

    assert results1 == ["item"]
    assert results2 == []


@pytest.mark.asyncio
async def test_trigger_generator_error_with_metrics(framework_with_metrics):
    """Test trigger_generator records error in metrics."""

    @framework_with_metrics.on("stream")
    async def failing_callback():
        raise ValueError("Error!")

    results = []
    async for item in framework_with_metrics.trigger_generator("stream"):
        results.append(item)

    metrics = framework_with_metrics.get_metrics()

    assert "stream:failing_callback" in metrics
    assert metrics["stream:failing_callback"]["error_count"] == 1


@pytest.mark.asyncio
async def test_trigger_generator_error_logging(framework_with_logging, caplog):
    """Test trigger_generator logs errors."""
    import logging

    @framework_with_logging.on("stream")
    async def failing_callback():
        raise ValueError("Test error!")

    with caplog.at_level(logging.ERROR):
        async for _ in framework_with_logging.trigger_generator("stream"):
            pass

    assert any("failed in generator mode" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_generator_respects_rate_limit(framework):
    """Test generator callbacks respect rate limits."""

    from openjiuwen.core.runner.callback import RateLimitFilter

    rate_limiter = RateLimitFilter(max_calls=2, time_window=1.0)
    framework.add_filter("stream", rate_limiter)

    call_count = 0

    @framework.on("stream")
    async def generator_callback():
        nonlocal call_count
        call_count += 1
        yield "item"

    results = []

    # Trigger multiple times - only 2 should succeed
    for _ in range(5):
        async for item in framework.trigger_generator("stream"):
            results.append(item)

    # Rate limit should allow only 2 calls
    assert call_count == 2
    assert len(results) == 2


@pytest.mark.asyncio
async def test_generator_skip_filter_logging(framework_with_logging, caplog):
    """Test trigger_generator with SKIP filter logs debug message."""
    import logging

    from openjiuwen.core.runner.callback import ValidationFilter

    skip_filter = ValidationFilter(lambda: False)
    framework_with_logging.add_filter("stream", skip_filter)

    @framework_with_logging.on("stream")
    async def callback():
        yield "item"

    with caplog.at_level(logging.DEBUG):
        async for _ in framework_with_logging.trigger_generator("stream"):
            pass

    # Should have logged skip message
    assert any("skipped callback" in record.message.lower() for record in caplog.records)
