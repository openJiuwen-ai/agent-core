# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.runner.message_queue_base import MessageQueueBase, QueueMessage
from openjiuwen.core.runner.runner_config import PulsarConfig


class TestPulsarSubscriptionDrain:

    @pytest.mark.asyncio
    async def test_deactivate_with_drain_waits_for_drain_event(self):
        from openjiuwen.extensions.message_queue.message_queue_pulsar import PulsarSubscription

        executor = ThreadPoolExecutor(max_workers=1)
        consumer = MagicMock()
        sub = PulsarSubscription("test-topic", consumer, executor)
        sub._active = True
        sub._handler = AsyncMock()
        sub._task = asyncio.create_task(asyncio.sleep(100))

        drain_wait_task = asyncio.create_task(sub.deactivate(drain_timeout=5.0))

        await asyncio.sleep(0.05)
        assert sub._draining is True

        sub._drain_event.set()
        await asyncio.sleep(0.05)

        try:
            await asyncio.wait_for(drain_wait_task, timeout=2.0)
        except asyncio.TimeoutError:
            pass

        if sub._task and not sub._task.done():
            sub._task.cancel()
            try:
                await sub._task
            except asyncio.CancelledError:
                pass

        executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_deactivate_without_drain_timeout_skips_drain(self):
        from openjiuwen.extensions.message_queue.message_queue_pulsar import PulsarSubscription

        executor = ThreadPoolExecutor(max_workers=1)
        consumer = MagicMock()
        sub = PulsarSubscription("test-topic", consumer, executor)
        sub._active = True
        sub._handler = AsyncMock()
        sub._task = asyncio.create_task(asyncio.sleep(100))

        await sub.deactivate()

        assert sub._draining is False

        if sub._task and not sub._task.done():
            sub._task.cancel()
            try:
                await sub._task
            except asyncio.CancelledError:
                pass

        executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_deactivate_drain_timeout_expires(self):
        from openjiuwen.extensions.message_queue.message_queue_pulsar import PulsarSubscription

        executor = ThreadPoolExecutor(max_workers=1)
        consumer = MagicMock()
        sub = PulsarSubscription("test-topic", consumer, executor)
        sub._active = True
        sub._handler = AsyncMock()
        sub._task = asyncio.create_task(asyncio.sleep(100))

        with patch("openjiuwen.extensions.message_queue.message_queue_pulsar.logger"):
            try:
                await asyncio.wait_for(sub.deactivate(drain_timeout=0.1), timeout=2.0)
            except asyncio.TimeoutError:
                sub._task.cancel()
                try:
                    await sub._task
                except asyncio.CancelledError:
                    pass

        executor.shutdown(wait=False)


class TestMessageQueuePulsarStopDrain:

    @pytest.mark.asyncio
    async def test_stop_passes_drain_timeout_to_subscriptions(self):
        from openjiuwen.extensions.message_queue.message_queue_pulsar import (
            MessageQueuePulsar,
            PulsarSubscription,
        )

        config = PulsarConfig(url="pulsar://localhost:6650")
        mq = MessageQueuePulsar(config)
        mq._is_running = True

        mock_sub = AsyncMock(spec=PulsarSubscription)
        mock_sub.deactivate = AsyncMock()
        mq._subs = {"test-topic": mock_sub}

        mq._producers = {}
        mq._executor = MagicMock()
        mq._client = MagicMock()

        await mq.stop(drain_timeout=3.0)

        mock_sub.deactivate.assert_called_once_with(drain_timeout=3.0)

    @pytest.mark.asyncio
    async def test_stop_default_drain_timeout_is_zero(self):
        from openjiuwen.extensions.message_queue.message_queue_pulsar import (
            MessageQueuePulsar,
            PulsarSubscription,
        )

        config = PulsarConfig(url="pulsar://localhost:6650")
        mq = MessageQueuePulsar(config)
        mq._is_running = True

        mock_sub = AsyncMock(spec=PulsarSubscription)
        mock_sub.deactivate = AsyncMock()
        mq._subs = {"test-topic": mock_sub}

        mq._producers = {}
        mq._executor = MagicMock()
        mq._client = MagicMock()

        await mq.stop()

        mock_sub.deactivate.assert_called_once_with(drain_timeout=None)
