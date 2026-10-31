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
    # 关键前置：stop() 遍历的是 _active_subscriptions，必须确保调用前非空，
    # 否则循环体一次都不执行，本用例对 stop() 的回归失去保护力。
    assert bus._active_subscriptions, "precondition: subscriptions must exist before stop()"
    bus._running = True
    await bus.stop()
    assert bus._mq._get_subscribed_topics() == [], "MQ subscription leak after stop()"
    assert bus._active_subscriptions == {}


@pytest.mark.asyncio
async def test_cleanup_unsubscribed_session_is_idempotent():
    bus = MessageBus()
    await bus.cleanup_session("never-existed")  # must not raise


@pytest.mark.asyncio
async def test_cleanup_keeps_reference_when_unsubscribe_fails():
    """unsubscribe 抛错时必须保留 _active_subscriptions 里的引用。

    若先 pop 再 unsubscribe，一旦 unsubscribe 失败，MQ 侧订阅残留而本地引用已丢，
    _ensure_subscription() 只查 _active_subscriptions 便判定“未订阅”而再次
    subscribe，撞上 topic 已存在的 ValueError，该 session 再也无法恢复通信。
    """
    bus = MessageBus()
    sid = "sess-Z"
    p2p = bus._get_p2p_topic(sid)
    await bus._ensure_subscription(p2p)
    assert p2p in bus._active_subscriptions

    async def boom(topic):
        raise RuntimeError("simulated unsubscribe failure")

    bus._mq.unsubscribe = boom
    await bus.cleanup_session(sid)  # 不向上抛，只记录日志

    # 引用仍在：后续可重试，且不会误判为“未订阅”而重复 subscribe
    assert p2p in bus._active_subscriptions, "reference dropped although unsubscribe failed"
