# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the MessageBus cleanup/stop subscription-leak fix.

Before the fix, cleanup_session()/stop() only deactivated subscriptions without
removing them from MessageQueueInMemory._subscribers, leaking 2 subscriptions per
session and causing ValueError on re-subscribe. After the fix they call
MessageQueueInMemory.unsubscribe() so the MQ subscription table is fully cleared.
"""
import pytest

from openjiuwen.core.multi_agent.team_runtime.message_bus import MessageBus


@pytest.mark.asyncio
async def test_cleanup_session_unsubscribes_mq():
    bus = MessageBus()
    sid = "sess-X"
    p2p = bus._get_p2p_topic(sid)
    pub = bus._get_pubsub_topic(sid)

    await bus._ensure_subscription(p2p)
    await bus._ensure_subscription(pub)
    assert set(bus._mq._get_subscribed_topics()) == {p2p, pub}

    await bus.cleanup_session(sid)
    assert bus._mq._get_subscribed_topics() == [], "MQ subscription leak after cleanup_session"
    assert bus._active_subscriptions == {}

    # re-subscribe same session must succeed (no ValueError)
    await bus._ensure_subscription(p2p)
    await bus._ensure_subscription(pub)
    assert set(bus._mq._get_subscribed_topics()) == {p2p, pub}


@pytest.mark.asyncio
async def test_stop_unsubscribes_mq():
    bus = MessageBus()
    sid = "sess-Y"
    p2p = bus._get_p2p_topic(sid)
    pub = bus._get_pubsub_topic(sid)
    await bus._ensure_subscription(p2p)
    await bus._ensure_subscription(pub)
    await bus.cleanup_session(sid)
    bus._running = True
    await bus.stop()
    assert bus._mq._get_subscribed_topics() == [], "MQ subscription leak after stop()"


@pytest.mark.asyncio
async def test_cleanup_unsubscribed_session_is_idempotent():
    bus = MessageBus()
    await bus.cleanup_session("never-existed")  # must not raise
