# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Exercise configured wire formats through real queue entry points."""

import asyncio
from typing import Literal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from openjiuwen.core.runner.drunner.dmessage_queue.message_queue_factory import MessageQueueFactory
from openjiuwen.core.runner.drunner.dmessage_queue.message_queue_fake import FakeMQ
from openjiuwen.core.runner.drunner.dmessage_queue.message_serializer import deserialize_message
from openjiuwen.core.runner.message_queue_base import QueueMessage
from openjiuwen.core.runner.runner_config import MessageQueueConfig, PulsarConfig
from openjiuwen.extensions.message_queue.message_queue_pulsar import MessageQueuePulsar


async def assert_fake_delivery(mq: FakeMQ) -> None:
    """Send reserved keys through serialization, subscription and the handler."""
    received = asyncio.get_running_loop().create_future()
    message = QueueMessage(payload={"__class__": "business", "__dsh_escaped__x": 1})

    async def handle(result: QueueMessage) -> None:
        """Capture the decoded message at the consumer boundary."""
        received.set_result(result)

    mq.start()
    sub = mq.subscribe("wire-test")
    sub.set_message_handler(handle)
    sub.activate()
    try:
        await mq.produce_message("wire-test", message)
        assert await asyncio.wait_for(received, timeout=2) == message
    finally:
        await mq.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("version,encoding", [(2, "literal"), (1, "escaped")])
async def test_factory_fake_queue_delivers_reserved_payload(
    version: Literal[1, 2],
    encoding: Literal["literal", "escaped"],
) -> None:
    """Factory configuration controls both the fake writer and subscription."""
    mq = MessageQueueFactory.create(MessageQueueConfig(
        type="fake", wire_version=version, legacy_key_encoding=encoding,
    ))
    await assert_fake_delivery(mq)


@pytest.mark.asyncio
@pytest.mark.parametrize("version,encoding", [(2, "literal"), (1, "escaped")])
async def test_factory_pulsar_producer_sends_configured_format(
    monkeypatch: pytest.MonkeyPatch,
    version: Literal[1, 2],
    encoding: Literal["literal", "escaped"],
) -> None:
    """Capture bytes at the Pulsar SDK boundary without a live broker."""
    client = MagicMock()
    monkeypatch.setattr("openjiuwen.extensions.message_queue.message_queue_pulsar.pulsar.Client", lambda url: client)
    mq = MessageQueueFactory.create(MessageQueueConfig(
        type="pulsar", pulsar_config=PulsarConfig(url="pulsar://localhost:6650"),
        wire_version=version, legacy_key_encoding=encoding,
    ))
    message = QueueMessage(payload={"__class__": "business", "__dsh_escaped__x": 1})
    mq.start()
    try:
        await mq.produce_message("wire-test", message)
        data = client.create_producer.return_value.send.call_args.args[0]
        assert deserialize_message(data, legacy_key_encoding=encoding) == message
        assert (b'DmqWireEnvelope' in data) == (version == 2)
    finally:
        await mq.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("version,encoding", [(2, "literal"), (1, "escaped")])
async def test_pulsar_fallback_preserves_wire_configuration(
    monkeypatch: pytest.MonkeyPatch,
    version: Literal[1, 2],
    encoding: Literal["literal", "escaped"],
) -> None:
    """Fallback must not reintroduce reserved-key failures or change key identity."""
    def fail_start(self: MessageQueuePulsar) -> None:
        """Simulate an unavailable broker."""
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(MessageQueuePulsar, "start", fail_start)
    mq, fallback = await MessageQueuePulsar.start_with_retry(
        PulsarConfig(url="pulsar://localhost:6650"), max_retries=1,
        wire_version=version, legacy_key_encoding=encoding,
    )
    assert fallback
    await assert_fake_delivery(mq)


@pytest.mark.asyncio
async def test_pulsar_subscription_decodes_frozen_upstream_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass unversioned escaped bytes through the real Pulsar receive loop."""
    client = MagicMock()
    monkeypatch.setattr("openjiuwen.extensions.message_queue.message_queue_pulsar.pulsar.Client", lambda url: client)
    client.subscribe.return_value.receive.return_value.data.return_value = (
        b'{"__class__":"DmqResponseMessage","message_id":"old-message","payload":'
        b'{"__dsh_escaped____class__":"business","__dsh_escaped____dsh_escaped__x":1}}'
    )
    mq = MessageQueueFactory.create(MessageQueueConfig(
        type="pulsar", pulsar_config=PulsarConfig(url="pulsar://localhost:6650"),
        wire_version=2, legacy_key_encoding="escaped",
    ))
    received = asyncio.get_running_loop().create_future()
    mq.start()
    sub = mq.subscribe("wire-test")

    async def handle(message: QueueMessage) -> None:
        """Stop after one acknowledged delivery."""
        received.set_result(message)
        sub._active = False

    sub.set_message_handler(handle)
    sub.activate()
    try:
        message = await asyncio.wait_for(received, timeout=2)
        assert message.payload == {"__class__": "business", "__dsh_escaped__x": 1}
        await sub._task
        client.subscribe.return_value.acknowledge.assert_called_once()
    finally:
        await mq.stop()


@pytest.mark.parametrize("options", [{"wire_version": 3}, {"legacy_key_encoding": "guess"}])
def test_invalid_wire_configuration_is_rejected(options: dict) -> None:
    """Reject unknown formats at configuration time."""
    with pytest.raises(ValidationError):
        MessageQueueConfig.model_validate(options)
