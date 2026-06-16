# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.runner.drunner.dmessage_queue.message_queue_fake import FakeMQ
from openjiuwen.core.runner.message_queue_base import MessageQueueBase
from openjiuwen.core.runner.runner_config import PulsarConfig


class TestUnsubscribeAllFakeMQ:

    @pytest.mark.asyncio
    async def test_unsubscribe_all_removes_all_topics(self):
        mq = FakeMQ()
        mq.start()

        sub1 = mq.subscribe("topic-a")
        sub2 = mq.subscribe("topic-b")
        assert len(mq._topics) == 2

        await mq.unsubscribe_all()
        assert len(mq._topics) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_all_on_empty_mq(self):
        mq = FakeMQ()
        mq.start()

        await mq.unsubscribe_all()
        assert len(mq._topics) == 0


class TestUnsubscribeAllPulsarMQ:

    @pytest.mark.asyncio
    async def test_unsubscribe_all_delegates_to_unsubscribe(self):
        from openjiuwen.extensions.message_queue.message_queue_pulsar import (
            MessageQueuePulsar,
            PulsarSubscription,
        )

        config = PulsarConfig(url="pulsar://localhost:6650")
        mq = MessageQueuePulsar(config)
        mq._is_running = True

        mock_sub1 = AsyncMock(spec=PulsarSubscription)
        mock_sub2 = AsyncMock(spec=PulsarSubscription)
        mq._subs = {"topic-a": mock_sub1, "topic-b": mock_sub2}

        mq._producers = {}
        mq._executor = MagicMock()
        mq._client = MagicMock()

        await mq.unsubscribe_all()

        assert len(mq._subs) == 0
        mock_sub1.deactivate.assert_called_once()
        mock_sub2.deactivate.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsubscribe_all_continues_on_single_failure(self):
        from openjiuwen.extensions.message_queue.message_queue_pulsar import (
            MessageQueuePulsar,
            PulsarSubscription,
        )

        config = PulsarConfig(url="pulsar://localhost:6650")
        mq = MessageQueuePulsar(config)
        mq._is_running = True

        mock_sub1 = AsyncMock(spec=PulsarSubscription)
        mock_sub1.deactivate = AsyncMock(side_effect=Exception("deactivate error"))
        mock_sub2 = AsyncMock(spec=PulsarSubscription)
        mq._subs = {"topic-a": mock_sub1, "topic-b": mock_sub2}

        mq._producers = {}
        mq._executor = MagicMock()
        mq._client = MagicMock()

        with patch("openjiuwen.extensions.message_queue.message_queue_pulsar.logger"):
            await mq.unsubscribe_all()

        mock_sub2.deactivate.assert_called_once()
