#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest
from typing import Any, AsyncIterator
import asyncio

from openjiuwen.core.runner.message_queue_base import StreamQueueMessage, InvokeQueueMessage, QueueMessage, MessageQueueBase
from openjiuwen.core.runner.message_queue_inmemory import MessageQueueInMemory
from openjiuwen.core.common.exception.status_code import StatusCode


class MockMessagehandler_stream:
    async def handle_message(self, request: Any) -> AsyncIterator[str]:
        for i in range(1, 10):
            response = f"MockMessagehandler_stream response for msg : {request}, i is {i}"
            yield response


class MockMessagehandler_invoke:
    async def handle_message(self, request: Any) -> str:
        return "MockMessagehandler_invoke response for msg : " + request


async def get_response(response):
    result = None
    try:
        result = await asyncio.wait_for(response, 1)
    except asyncio.TimeoutError:
        result = None
    finally:
        return result


@pytest.mark.asyncio
class TestMessageQueue:

    async def messagequeue_common(self, mq: MessageQueueBase):
        mq.start()

        # stream handler
        subscription1 = mq.subscribe("topic_stream")
        subscription1.set_message_handler(MockMessagehandler_stream().handle_message)
        subscription1.activate()
        # invoke handler
        subscription2 = mq.subscribe("topic_invoke")
        subscription2.set_message_handler(MockMessagehandler_invoke().handle_message)
        subscription2.activate()

        # <1.1> send stream request to stream handler
        message = StreamQueueMessage()
        message.payload = "上海温度多少"
        message.response = asyncio.Future()
        await mq.produce_message("topic_stream", message)
        response = await get_response(message.response)
        assert response is not None
        assert message.error_code == StatusCode.SUCCESS.code
        assert message.error_msg == ""
        i = 1
        async for ret in response:
            assert ret == f"MockMessagehandler_stream response for msg : 上海温度多少, i is {i}"
            i += 1

        # # <1.2> send invoke request to stream handler
        message1 = InvokeQueueMessage()
        message1.payload = "上海温度多少"
        await mq.produce_message("topic_stream", message1)
        response = await get_response(message1.response)
        assert response is None
        assert message1.error_code == StatusCode.ERROR.code

        # <1.3> send publish request to stream handler
        message2 = QueueMessage()
        message2.payload = "上海温度多少"
        await mq.produce_message("topic_stream", message2)
        assert message2.error_code == StatusCode.SUCCESS.code
        assert message2.error_msg == ""

        # <2.1> send inkoke request to invkoke handler
        message3 = InvokeQueueMessage()
        message3.payload = "北京温度多少"
        message3.response = asyncio.Future()
        await mq.produce_message("topic_invoke", message3)
        response = await get_response(message3.response)
        assert response is not None
        assert response == "MockMessagehandler_invoke response for msg : 北京温度多少"
        assert message3.error_code == StatusCode.SUCCESS.code
        assert message3.error_msg == ""

        # <2.2> send stream request to invkoke handler
        message4 = StreamQueueMessage()
        message4.payload = "北京温度多少"
        await mq.produce_message("topic_invoke", message4)
        response = await get_response(message4.response)
        assert response is None
        assert message4.error_code == StatusCode.ERROR.code

        # <2.3> send publish request to invkoke handler
        message5 = QueueMessage()
        message5.payload = "北京温度多少"
        await mq.produce_message("topic_invoke", message5)
        assert message5.error_code == StatusCode.SUCCESS.code
        assert message5.error_msg == ""

        # agnetGroup 2
        await mq.unsubscribe("topic_invoke")
        await mq.unsubscribe("topic_stream")

        await mq.stop()

    async def test_messagequeue_inmemory(self):
        mq = MessageQueueInMemory()
        await self.messagequeue_common(mq)
