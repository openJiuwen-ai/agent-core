# coding: utf-8
"""Tests for messager transports."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.messager import (
    create_messager,
    InProcessMessager,
    Messager,
    MessagerTransportConfig,
    PyZmqMessager,
    SubscriptionHandle,
)
from openjiuwen.agent_teams.messager.inprocess import cleanup_inprocess_bus
from openjiuwen.agent_teams.schema.events import BaseEventMessage, EventMessage


class _SampleEvent(BaseEventMessage):
    team_name: str = "team-1"
    detail: str = "test"


@pytest.fixture(autouse=True)
def _clean_bus():
    """Reset the process-global bus between tests."""
    cleanup_inprocess_bus()
    yield
    cleanup_inprocess_bus()


# === InProcessMessager ===


@pytest.mark.asyncio
async def test_inprocess_messager_is_messager() -> None:
    config = MessagerTransportConfig(
        backend="inprocess",
        team_id="team-1",
        node_id="worker",
    )
    transport = InProcessMessager(config=config)
    assert isinstance(transport, Messager)


@pytest.mark.asyncio
async def test_inprocess_pubsub_delivers_to_subscriber() -> None:
    """publish fans out to all subscribed handlers."""
    received: list[BaseEventMessage] = []

    async def handler(msg: BaseEventMessage) -> None:
        received.append(msg)

    leader = InProcessMessager(config=MessagerTransportConfig(node_id="leader"))
    worker = InProcessMessager(config=MessagerTransportConfig(node_id="worker"))

    await worker.subscribe("topic:team", handler)

    event = _SampleEvent()
    await leader.publish("topic:team", event)

    assert len(received) == 1
    assert received[0] is event


@pytest.mark.asyncio
async def test_inprocess_publish_stamps_sender_id() -> None:
    """publish must stamp sender_id so subscribers can filter self-events."""
    received: list[EventMessage] = []

    async def handler(msg: EventMessage) -> None:
        received.append(msg)

    leader = InProcessMessager(config=MessagerTransportConfig(node_id="leader"))
    worker = InProcessMessager(config=MessagerTransportConfig(node_id="worker"))

    await worker.subscribe("topic:team", handler)

    msg = EventMessage(event_type="team_cleaned", payload={"team_name": "t"})
    assert msg.sender_id == ""
    await leader.publish("topic:team", msg)

    assert len(received) == 1
    assert received[0].sender_id == "leader"


@pytest.mark.asyncio
async def test_inprocess_pubsub_fan_out() -> None:
    """Multiple subscribers on the same topic all receive the message."""
    received_a: list[BaseEventMessage] = []
    received_b: list[BaseEventMessage] = []

    async def handler_a(msg: BaseEventMessage) -> None:
        received_a.append(msg)

    async def handler_b(msg: BaseEventMessage) -> None:
        received_b.append(msg)

    pub = InProcessMessager(config=MessagerTransportConfig(node_id="pub"))
    sub_a = InProcessMessager(config=MessagerTransportConfig(node_id="sub-a"))
    sub_b = InProcessMessager(config=MessagerTransportConfig(node_id="sub-b"))

    await sub_a.subscribe("t", handler_a)
    await sub_b.subscribe("t", handler_b)

    await pub.publish("t", _SampleEvent())

    assert len(received_a) == 1
    assert len(received_b) == 1


@pytest.mark.asyncio
async def test_inprocess_unsubscribe_stops_delivery() -> None:
    received: list[BaseEventMessage] = []

    async def handler(msg: BaseEventMessage) -> None:
        received.append(msg)

    m = InProcessMessager(config=MessagerTransportConfig(node_id="a"))
    await m.subscribe("t", handler)
    await m.unsubscribe("t")

    await m.publish("t", _SampleEvent())
    assert len(received) == 0


@pytest.mark.asyncio
async def test_inprocess_p2p_delivers_to_handler() -> None:
    """send delivers to the registered direct-message handler."""
    received: list[BaseEventMessage] = []

    async def handler(msg: BaseEventMessage) -> None:
        received.append(msg)

    receiver = InProcessMessager(config=MessagerTransportConfig(node_id="receiver"))
    sender = InProcessMessager(config=MessagerTransportConfig(node_id="sender"))

    await receiver.register_direct_message_handler(handler)

    event = _SampleEvent()
    await sender.send("receiver", event)

    assert len(received) == 1
    assert received[0] is event


@pytest.mark.asyncio
async def test_inprocess_unregister_p2p_stops_delivery() -> None:
    received: list[BaseEventMessage] = []

    async def handler(msg: BaseEventMessage) -> None:
        received.append(msg)

    m = InProcessMessager(config=MessagerTransportConfig(node_id="x"))
    await m.register_direct_message_handler(handler)
    await m.unregister_direct_message_handler()

    await m.send("x", _SampleEvent())
    assert len(received) == 0


@pytest.mark.asyncio
async def test_inprocess_pubsub_handler_error_does_not_block_others() -> None:
    """A failing handler should not prevent other subscribers from receiving."""
    received: list[BaseEventMessage] = []

    async def bad_handler(msg: BaseEventMessage) -> None:
        raise RuntimeError("boom")

    async def good_handler(msg: BaseEventMessage) -> None:
        received.append(msg)

    bad = InProcessMessager(config=MessagerTransportConfig(node_id="bad"))
    good = InProcessMessager(config=MessagerTransportConfig(node_id="good"))
    pub = InProcessMessager(config=MessagerTransportConfig(node_id="pub"))

    await bad.subscribe("t", bad_handler)
    await good.subscribe("t", good_handler)

    await pub.publish("t", _SampleEvent())
    assert len(received) == 1


# === Factory ===


def test_create_messager_builds_inprocess() -> None:
    transport = create_messager(MessagerTransportConfig(backend="inprocess"))
    assert isinstance(transport, InProcessMessager)


def test_create_messager_builds_pyzmq() -> None:
    transport = create_messager(
        MessagerTransportConfig(
            backend="pyzmq",
            team_id="team-1",
            node_id="leader",
            direct_addr="tcp://127.0.0.1:19001",
            pubsub_publish_addr="tcp://127.0.0.1:19100",
            pubsub_subscribe_addr="tcp://127.0.0.1:19101",
        )
    )
    assert isinstance(transport, PyZmqMessager)
    assert isinstance(transport, Messager)


# === Model roundtrip ===


def test_models_roundtrip_with_pydantic_serialization() -> None:
    subscription = SubscriptionHandle(
        subscription_id="sub-1",
        topic="topic",
        agent_id="worker",
    )

    assert SubscriptionHandle.model_validate(
        subscription.model_dump(mode="python"),
    ).agent_id == "worker"
