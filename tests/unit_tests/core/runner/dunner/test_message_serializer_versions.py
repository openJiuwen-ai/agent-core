# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Explicit wire versions keep business keys literal across rolling upgrades."""

import datetime
import json

import pytest

from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqResponseMessage
from openjiuwen.core.runner.drunner.dmessage_queue.message_serializer import (
    TYPE_REGISTRY,
    deserialize_message,
    serialize_message,
)


def _old_decode(value: object) -> object:
    """Freeze the legacy marker-based consumer, with no new format support."""
    if isinstance(value, list):
        return [_old_decode(item) for item in value]
    if isinstance(value, dict):
        if value.get("__type__") == "datetime":
            return datetime.datetime.fromisoformat(value["value"])
        fields = {key: _old_decode(item) for key, item in value.items() if key != "__class__"}
        if "__class__" in value:
            name = value["__class__"]
            if name not in TYPE_REGISTRY:
                raise ValueError(f"Unknown payload class: {name}")
            return TYPE_REGISTRY[name].model_validate(fields)
        return fields
    return value


def test_legacy_sender_prefix_keys_are_not_unescaped() -> None:
    # A fixed historical message, independent of the current writer.
    data = b'{"__class__":"DmqResponseMessage","payload":{"__dsh_escaped__x":1,"x":2}}'
    assert deserialize_message(data).payload == {"__dsh_escaped__x": 1, "x": 2}


def test_default_new_writer_is_readable_by_legacy_receiver() -> None:
    message = DmqResponseMessage(payload={"__dsh_escaped__x": 1, "__type__": "business", "plain": [1, 2]})
    data = serialize_message(message)
    assert "version" not in json.loads(data)
    assert _old_decode(json.loads(data)) == message
    assert deserialize_message(data) == message


def test_version_two_preserves_reserved_keys_nested_models_and_datetime() -> None:
    timestamp = datetime.datetime(2026, 11, 3, tzinfo=datetime.timezone.utc)
    payload = {
        "__class__": "plain",
        "__type__": "datetime",
        "value": "not-a-date",
        "__dsh_escaped__x": 1,
        "nested": [{"__type__": "plain_dict", "items": [1, 2]}, {"__class__": "DmqWireEnvelope"}],
        "model": DmqResponseMessage(payload={"__class__": "nested-plain"}),
        "timestamp": timestamp,
    }
    message = DmqResponseMessage(payload=payload)
    data = serialize_message(message, wire_version=2)
    assert deserialize_message(data) == message
    # Old peers cannot decode v2: fail explicitly rather than silently alter keys.
    with pytest.raises(ValueError, match="DmqWireEnvelope"):
        _old_decode(json.loads(data))


@pytest.mark.parametrize("payload", [{"__class__": "plain"}, {"__type__": "datetime", "value": "bad"}])
def test_legacy_writes_reject_payload_old_receivers_would_corrupt(payload: dict) -> None:
    with pytest.raises(ValueError, match="wire_version=2"):
        serialize_message(DmqResponseMessage(payload={"nested": payload}))


def test_unknown_wire_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported MQ wire version"):
        deserialize_message(b'{"__class__":"DmqWireEnvelope","version":3,"message":{}}')
