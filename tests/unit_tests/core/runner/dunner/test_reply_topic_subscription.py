#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqResponseMessage, DMessageType
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig, DistributedConfig, MessageQueueConfig


@pytest.mark.asyncio
class TestReplyTopicSubscription:
    @staticmethod
    def setup_method():
        fake_mq = RunnerConfig(
            distributed_mode=True,
            distributed_config=DistributedConfig(
                request_timeout=5.0,
                message_queue_config=MessageQueueConfig(
                    type="fake",
                )
            )
        )
        Runner.set_config(fake_mq)

    async def test_normal_message_reception(self):
        """Test normal registration and message reception process"""
        try:
            await Runner.start()
            reply_sub = Runner.system_reply_sub
            message_id = "test_msg_123"
            remote_id = "agent_456"
            collector = await reply_sub.register_collector(message_id, remote_id)


            # Send test message
            msg = DmqResponseMessage(
                type=DMessageType.OUTPUT,
                sender_id=remote_id,
                message_id=message_id,
                payload="test_payload",
                last_chunk=True,
            )
            await reply_sub.on_message(msg)

            # Verify message reception
            result = await asyncio.wait_for(collector.result(), timeout=1.0)
            assert result == "test_payload"
        finally:
            await Runner.stop()

    async def test_unregistered_message_handling(self):
        """Test message handling when collector is not registered"""
        try:
            await Runner.start()
            unreg_msg = DmqResponseMessage(
                type=DMessageType.OUTPUT,
                sender_id="unknown_agent",
                message_id="unknown_msg",
                payload="unregistered"
            )
            # Unregistered message handling should not cause exceptions
            reply_sub = Runner.system_reply_sub
            await reply_sub.on_message(unreg_msg)
        finally:
            await Runner.stop()
