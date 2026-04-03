# coding: utf-8
"""Tests for messager transports."""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.messager import (
    create_messager,
    Messager,
    MessagerTransportConfig,
    PyZmqMessager,
    SubscriptionHandle,
    TeamRuntimeMessager,
)
from openjiuwen.agent_teams.schema.events import BaseEventMessage


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def start(self) -> None:
        self.calls.append(("start", {}))

    async def stop(self) -> None:
        self.calls.append(("stop", {}))

    async def send(self, **kwargs):
        self.calls.append(("send", kwargs))
        return {"ok": True}

    async def publish(self, **kwargs) -> None:
        self.calls.append(("publish", kwargs))

    async def subscribe(self, **kwargs) -> None:
        self.calls.append(("subscribe", kwargs))

    async def unsubscribe(self, **kwargs) -> None:
        self.calls.append(("unsubscribe", kwargs))


class _SampleEvent(BaseEventMessage):
    team_id: str = "team-1"
    detail: str = "test"


async def _noop_handler(
    message: BaseEventMessage,
) -> None:
    del message


# === TeamRuntimeMessager ===


@pytest.mark.asyncio
async def test_team_runtime_transport_maps_runtime_calls() -> None:
    """subscribe/unsubscribe delegate to runtime with node_id."""
    runtime = _FakeRuntime()
    config = MessagerTransportConfig(
        backend="team_runtime",
        team_id="team-1",
        node_id="worker",
    )
    transport = TeamRuntimeMessager(
        runtime=runtime, config=config,
    )
    assert isinstance(transport, Messager)

    event = _SampleEvent()

    await transport.send("worker", event)
    await transport.publish("team:team-1:broadcast", event)
    await transport.subscribe(
        "team:team-1:broadcast", _noop_handler,
    )
    await transport.unsubscribe("team:team-1:broadcast")

    assert runtime.calls[0][0] == "send"
    assert runtime.calls[1][0] == "publish"
    assert runtime.calls[1][1]["topic_id"] == (
        "team:team-1:broadcast"
    )
    assert runtime.calls[2] == (
        "subscribe",
        {"agent_id": "worker", "topic": "team:team-1:broadcast"},
    )
    assert runtime.calls[3] == (
        "unsubscribe",
        {"agent_id": "worker", "topic": "team:team-1:broadcast"},
    )


# === Factory ===


def test_create_transport_builds_pyzmq_backend() -> None:
    """create_messager_transport returns PyZmqMessagerTransport for pyzmq."""
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
    """Pydantic models serialize and deserialize correctly."""
    subscription = SubscriptionHandle(
        subscription_id="sub-1",
        topic="topic",
        agent_id="worker",
    )

    assert SubscriptionHandle.model_validate(
        subscription.model_dump(mode="python"),
    ).agent_id == "worker"
