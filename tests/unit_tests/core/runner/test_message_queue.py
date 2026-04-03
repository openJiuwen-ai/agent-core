#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest
from typing import Any, AsyncIterator
import asyncio

from openjiuwen.core.runner.message_queue_base import StreamQueueMessage, InvokeQueueMessage, QueueMessage, MessageQueueBase
from openjiuwen.core.runner.message_queue_inmemory import MessageQueueInMemory
from openjiuwen.core.common.exception.codes import StatusCode


class MockMessageHandlerStream:
    async def handle_event(self, request: Any) -> AsyncIterator[str]:
        for i in range(1, 10):
            response = f"MockMessageHandlerStream response for msg : {request}, i is {i}"
            yield response


class MockMessageHandlerInvoke:
    async def handle_event(self, request: Any) -> str:
        return "MockMessageHandlerInvoke response for msg : " + request


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

    async def message_queue_common(self, mq: MessageQueueBase):
        mq.start()

        # stream handler
        subscription1 = mq.subscribe("topic_stream")
        subscription1.set_message_handler(MockMessageHandlerStream().handle_event)
        subscription1.activate()
        # invoke handler
        subscription2 = mq.subscribe("topic_invoke")
        subscription2.set_message_handler(MockMessageHandlerInvoke().handle_event)
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
            assert ret == f"MockMessageHandlerStream response for msg : 上海温度多少, i is {i}"
            i += 1

        # # <1.2> send invoke request to stream handler
        message1 = InvokeQueueMessage()
        message1.payload = "上海温度多少"
        await mq.produce_message("topic_stream", message1)
        response = await get_response(message1.response)
        assert response is None
        assert message1.error_code == StatusCode.MESSAGE_QUEUE_MESSAGE_CONSUME_ERROR.code

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
        assert response == "MockMessageHandlerInvoke response for msg : 北京温度多少"
        assert message3.error_code == StatusCode.SUCCESS.code
        assert message3.error_msg == ""

        # <2.2> send stream request to invkoke handler
        message4 = StreamQueueMessage()
        message4.payload = "北京温度多少"
        await mq.produce_message("topic_invoke", message4)
        response = await get_response(message4.response)
        assert response is None
        assert message4.error_code == StatusCode.MESSAGE_QUEUE_MESSAGE_CONSUME_ERROR.code

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

    async def test_message_queue_inmemory(self):
        mq = MessageQueueInMemory()
        await self.message_queue_common(mq)

    # ------------------------------------------------------------------
    # Regression tests for nested-send deadlock fix
    # Bug: SubscriptionInMemory._consume_message directly awaited the handler
    #      coroutine, blocking the consume loop. When the handler called
    #      produce_message() on the same queue and awaited the response
    #      (nested send), the new message could never be consumed, causing
    #      a permanent deadlock.
    # Fix: Use asyncio.create_task so the handler runs in a separate Task
    #      and the consume loop remains free to process new messages.
    # ------------------------------------------------------------------

    async def test_nested_send_no_deadlock(self):
        """
        Handler produces a second message onto the same queue and awaits its
        response (simulates CommunicableAgent nested send: Planner -> Coder).
        Before fix: hangs forever. After fix: returns correct result.
        """
        TOPIC = "test_nested_send"
        TIMEOUT = 5.0
        mq = MessageQueueInMemory(queue_max_size=100, timeout=TIMEOUT)
        call_count = 0

        async def outer_handler(payload):
            import uuid
            inner_msg = InvokeQueueMessage()
            inner_msg.message_id = str(uuid.uuid4())
            inner_msg.payload = "inner"
            await mq.produce_message(TOPIC, inner_msg)
            inner_result = await inner_msg.response
            return f"outer({inner_result})"

        async def inner_handler(payload):
            return "inner_done"

        async def dispatch(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await outer_handler(payload)
            return await inner_handler(payload)

        subscription = mq.subscribe(TOPIC)
        subscription.set_message_handler(dispatch)
        subscription.activate()
        mq.start()

        import uuid
        outer_msg = InvokeQueueMessage()
        outer_msg.message_id = str(uuid.uuid4())
        outer_msg.payload = "outer"
        await mq.produce_message(TOPIC, outer_msg)

        try:
            result = await asyncio.wait_for(outer_msg.response, timeout=TIMEOUT)
        finally:
            await mq.stop()

        assert result == "outer(inner_done)"
        assert call_count == 2

    async def test_nested_send_three_levels(self):
        """
        Three-level nesting: A -> B -> C.
        Verifies the fix holds for deeper nesting chains.
        """
        TOPIC = "test_nested_three"
        TIMEOUT = 5.0
        mq = MessageQueueInMemory(queue_max_size=100, timeout=TIMEOUT)
        call_count = 0
        import uuid

        async def level_c(payload): return "C"

        async def level_b(payload):
            msg = InvokeQueueMessage()
            msg.message_id = str(uuid.uuid4())
            msg.payload = "c"
            await mq.produce_message(TOPIC, msg)
            return f"B({await msg.response})"

        async def level_a(payload):
            msg = InvokeQueueMessage()
            msg.message_id = str(uuid.uuid4())
            msg.payload = "b"
            await mq.produce_message(TOPIC, msg)
            return f"A({await msg.response})"

        async def dispatch(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1: return await level_a(payload)
            if call_count == 2: return await level_b(payload)
            return await level_c(payload)

        subscription = mq.subscribe(TOPIC)
        subscription.set_message_handler(dispatch)
        subscription.activate()
        mq.start()

        root_msg = InvokeQueueMessage()
        root_msg.message_id = str(uuid.uuid4())
        root_msg.payload = "a"
        await mq.produce_message(TOPIC, root_msg)

        try:
            result = await asyncio.wait_for(root_msg.response, timeout=TIMEOUT)
        finally:
            await mq.stop()

        assert result == "A(B(C))"
        assert call_count == 3

    async def test_nested_send_task_done_called_once(self):
        """
        Verifies task_done() is called exactly once per message.
        asyncio.Queue raises ValueError if called more than once.
        """
        TOPIC = "test_task_done"
        TIMEOUT = 5.0
        mq = MessageQueueInMemory(queue_max_size=100, timeout=TIMEOUT)
        call_count = 0
        import uuid

        async def outer(payload):
            inner_msg = InvokeQueueMessage()
            inner_msg.message_id = str(uuid.uuid4())
            inner_msg.payload = "inner"
            await mq.produce_message(TOPIC, inner_msg)
            res = await inner_msg.response
            return f"ok({res})"

        async def dispatch(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await outer(payload)
            return "inner_result"

        subscription = mq.subscribe(TOPIC)
        subscription.set_message_handler(dispatch)
        subscription.activate()
        mq.start()

        msg = InvokeQueueMessage()
        msg.message_id = str(uuid.uuid4())
        msg.payload = "start"
        await mq.produce_message(TOPIC, msg)

        try:
            result = await asyncio.wait_for(msg.response, timeout=TIMEOUT)
            await asyncio.sleep(0.1)  # let background tasks settle
        finally:
            await mq.stop()

        assert result == "ok(inner_result)"

