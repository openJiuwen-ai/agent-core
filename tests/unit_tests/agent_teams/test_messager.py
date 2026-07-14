# coding: utf-8
"""Tests for messager transports."""
from __future__ import annotations

import json
from types import SimpleNamespace

import aiohttp
import pytest

from openjiuwen.agent_teams.messager import (
    create_messager,
    InProcessMessager,
    Messager,
    MessagerTransportConfig,
    PyZmqMessager,
    SubscriptionHandle,
)
from openjiuwen.agent_teams.messager.hybrid import HybridMessager, WebSocketEventPublisher
from openjiuwen.agent_teams.messager.inprocess import cleanup_inprocess_bus
from openjiuwen.agent_teams.schema.events import BaseEventMessage, EventMessage, TeamTopic


class _SampleEvent(BaseEventMessage):
    team_name: str = "team-1"
    detail: str = "test"


class _FakeEventPublisher:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.published: list[tuple[str, EventMessage]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def publish(self, topic_id: str, message: EventMessage) -> None:
        self.published.append((topic_id, message))


@pytest.fixture(autouse=True)
def _clean_bus():
    """Reset the process-global bus between tests."""
    cleanup_inprocess_bus()
    yield
    cleanup_inprocess_bus()


# === InProcessMessager ===


@pytest.mark.asyncio
@pytest.mark.level0
async def test_inprocess_messager_is_messager() -> None:
    config = MessagerTransportConfig(
        backend="inprocess",
        team_id="team-1",
        node_id="worker",
    )
    transport = InProcessMessager(config=config)
    assert isinstance(transport, Messager)


@pytest.mark.asyncio
@pytest.mark.level0
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
@pytest.mark.level0
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
@pytest.mark.level0
async def test_external_publisher_publishes_without_local_messager() -> None:
    publisher = _FakeEventPublisher()
    hybrid = HybridMessager(
        publisher=publisher,
        sender_id="external-cli",
    )

    await hybrid.start()
    message = EventMessage(event_type="message", payload={"team_name": "team-1"})
    await hybrid.publish("topic:message", message)

    assert publisher.started is True
    assert publisher.published[0][0] == "topic:message"
    assert publisher.published[0][1].sender_id == "external-cli"

    await hybrid.stop()
    assert publisher.stopped is True


@pytest.mark.level0
def test_websocket_publisher_uses_standard_interact_payload() -> None:
    publisher = WebSocketEventPublisher(
        url="ws://gateway:19000/ws",
        session_id="session-1",
        team_name="team-1",
        request_timeout=1.0,
    )
    topic = TeamTopic.MESSAGE.build("session-1", "team-1")
    request = publisher._build_request(
        topic,
        EventMessage(event_type="message", payload={"team_name": "team-1"}),
        "request-1",
    )

    assert request["session_id"] == "session-1"
    assert request["channel"] == "web"
    assert request["method"] == "chat.send"
    assert request["is_stream"] is True
    assert request["params"]["mode"] == "team"
    assert request["params"]["team"] is True
    assert request["params"]["query"] == {
        "type": "team.external_event",
        "topic": TeamTopic.MESSAGE.value,
        "event": {
            "event_type": "message",
            "payload": {"team_name": "team-1"},
            "sender_id": "",
        },
    }


@pytest.mark.asyncio
@pytest.mark.level0
async def test_websocket_publisher_connects_once_when_started() -> None:
    publisher = WebSocketEventPublisher(
        url="ws://gateway:19000/ws",
        session_id="session-1",
        team_name="team-1",
        request_timeout=1.0,
    )

    class FakeWebSocket:
        def __init__(self) -> None:
            self.closed = False
            self.responses = []

        async def send_str(self, data: str) -> None:
            request = json.loads(data)
            self.responses.append(
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {
                            "request_id": request["request_id"],
                            "response_kind": "e2a.complete",
                            "status": "succeeded",
                        }
                    ),
                )
            )

        async def receive(self):
            return self.responses.pop(0)

        async def close(self) -> None:
            self.closed = True

    class FakeSession:
        def __init__(self) -> None:
            self.closed = False
            self.ws = FakeWebSocket()
            self.connect_count = 0

        async def ws_connect(self, _url: str, *, heartbeat: float):
            self.connect_count += 1
            return self.ws

        async def close(self) -> None:
            self.closed = True

    session = FakeSession()
    publisher._session = session
    await publisher.start()

    topic = TeamTopic.MESSAGE.build("session-1", "team-1")
    message = EventMessage(event_type="message", payload={"team_name": "team-1"})
    await publisher.publish(topic, message)
    await publisher.publish(topic, message)

    assert session.connect_count == 1
    await publisher.stop()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_websocket_publisher_requires_start_before_publish() -> None:
    publisher = WebSocketEventPublisher(
        url="ws://gateway:19000/ws",
        session_id="session-1",
        team_name="team-1",
        request_timeout=1.0,
    )
    topic = TeamTopic.MESSAGE.build("session-1", "team-1")
    message = EventMessage(event_type="message", payload={"team_name": "team-1"})

    with pytest.raises(RuntimeError, match="is not started"):
        await publisher.publish(topic, message)


@pytest.mark.level0
def test_websocket_publisher_waits_for_e2a_complete() -> None:
    assert WebSocketEventPublisher._is_successful_response(
        {
            "response_kind": "e2a.chunk",
            "status": "in_progress",
        }
    ) is False
    assert WebSocketEventPublisher._is_successful_response(
        {
            "response_kind": "e2a.complete",
            "status": "succeeded",
        }
    ) is True
    with pytest.raises(RuntimeError, match="event rejected"):
        WebSocketEventPublisher._is_successful_response(
            {
                "response_kind": "e2a.error",
                "status": "failed",
                "body": {"message": "event rejected"},
            }
        )


@pytest.mark.asyncio
@pytest.mark.level0
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
@pytest.mark.level1
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
@pytest.mark.level1
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
@pytest.mark.level1
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
@pytest.mark.level1
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


@pytest.mark.level1
def test_create_messager_builds_inprocess() -> None:
    transport = create_messager(MessagerTransportConfig(backend="inprocess"))
    assert isinstance(transport, InProcessMessager)


@pytest.mark.level1
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


@pytest.mark.level1
def test_models_roundtrip_with_pydantic_serialization() -> None:
    subscription = SubscriptionHandle(
        subscription_id="sub-1",
        topic="topic",
        agent_id="worker",
    )

    assert SubscriptionHandle.model_validate(
        subscription.model_dump(mode="python"),
    ).agent_id == "worker"
