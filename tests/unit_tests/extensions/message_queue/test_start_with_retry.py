# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.runner.drunner.dmessage_queue.message_queue_fake import FakeMQ
from openjiuwen.core.runner.message_queue_base import MessageQueueBase, QueueMessage
from openjiuwen.core.runner.runner_config import PulsarConfig


class TestStartWithRetrySuccess:

    @pytest.mark.asyncio
    async def test_returns_pulsar_instance_on_first_success(self):
        config = PulsarConfig(url="pulsar://localhost:6650")
        with patch(
            "openjiuwen.extensions.message_queue.message_queue_pulsar.MessageQueuePulsar.start"
        ):
            from openjiuwen.extensions.message_queue.message_queue_pulsar import (
                MessageQueuePulsar,
            )

            mq, is_fallback = await MessageQueuePulsar.start_with_retry(
                config, max_retries=3, retry_delay=0.01
            )
            assert is_fallback is False
            assert isinstance(mq, MessageQueuePulsar)

    @pytest.mark.asyncio
    async def test_returns_not_fallback_on_success(self):
        config = PulsarConfig(url="pulsar://localhost:6650")
        with patch(
            "openjiuwen.extensions.message_queue.message_queue_pulsar.MessageQueuePulsar.start"
        ):
            from openjiuwen.extensions.message_queue.message_queue_pulsar import (
                MessageQueuePulsar,
            )

            _, is_fallback = await MessageQueuePulsar.start_with_retry(
                config, max_retries=3, retry_delay=0.01
            )
            assert is_fallback is False


class TestStartWithRetryFallback:

    @pytest.mark.asyncio
    async def test_falls_back_to_fake_mq_on_all_failures(self):
        config = PulsarConfig(url="pulsar://invalid:6650")
        with patch(
            "openjiuwen.extensions.message_queue.message_queue_pulsar.MessageQueuePulsar.start",
            side_effect=Exception("connection refused"),
        ):
            from openjiuwen.extensions.message_queue.message_queue_pulsar import (
                MessageQueuePulsar,
            )

            mq, is_fallback = await MessageQueuePulsar.start_with_retry(
                config, max_retries=2, retry_delay=0.01, fallback_to_fake=True
            )
            assert is_fallback is True
            assert isinstance(mq, FakeMQ)

    @pytest.mark.asyncio
    async def test_publishes_fallback_interrupt_message(self):
        config = PulsarConfig(url="pulsar://invalid:6650")
        with patch(
            "openjiuwen.extensions.message_queue.message_queue_pulsar.MessageQueuePulsar.start",
            side_effect=Exception("connection refused"),
        ), patch.object(FakeMQ, "produce_message", autospec=True) as mock_produce:
            from openjiuwen.extensions.message_queue.message_queue_pulsar import (
                MessageQueuePulsar,
            )

            mq, is_fallback = await MessageQueuePulsar.start_with_retry(
                config, max_retries=2, retry_delay=0.01, fallback_to_fake=True
            )
            assert is_fallback is True
            mock_produce.assert_called_once()
            call_args = mock_produce.call_args
            assert call_args[0][1] == "interrupt"
            assert call_args[0][2].message_id == "__mq_fallback__"

    @pytest.mark.asyncio
    async def test_raises_when_fallback_disabled(self):
        config = PulsarConfig(url="pulsar://invalid:6650")
        with patch(
            "openjiuwen.extensions.message_queue.message_queue_pulsar.MessageQueuePulsar.start",
            side_effect=Exception("connection refused"),
        ):
            from openjiuwen.extensions.message_queue.message_queue_pulsar import (
                MessageQueuePulsar,
            )

            with pytest.raises(Exception, match="connection refused"):
                await MessageQueuePulsar.start_with_retry(
                    config,
                    max_retries=2,
                    retry_delay=0.01,
                    fallback_to_fake=False,
                )


class TestStartWithRetryRetries:

    @pytest.mark.asyncio
    async def test_retries_specified_number_of_times(self):
        config = PulsarConfig(url="pulsar://invalid:6650")
        with patch(
            "openjiuwen.extensions.message_queue.message_queue_pulsar.MessageQueuePulsar.start",
            side_effect=Exception("fail"),
        ) as mock_start:
            from openjiuwen.extensions.message_queue.message_queue_pulsar import (
                MessageQueuePulsar,
            )

            await MessageQueuePulsar.start_with_retry(
                config, max_retries=3, retry_delay=0.01, fallback_to_fake=True
            )
            assert mock_start.call_count == 3

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt(self):
        config = PulsarConfig(url="pulsar://localhost:6650")
        with patch(
            "openjiuwen.extensions.message_queue.message_queue_pulsar.MessageQueuePulsar.start",
            side_effect=[Exception("fail"), None],
        ):
            from openjiuwen.extensions.message_queue.message_queue_pulsar import (
                MessageQueuePulsar,
            )

            mq, is_fallback = await MessageQueuePulsar.start_with_retry(
                config, max_retries=3, retry_delay=0.01
            )
            assert is_fallback is False
            assert isinstance(mq, MessageQueuePulsar)
