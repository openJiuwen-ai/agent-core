# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Plain payload dicts may use the serializer's marker names as data keys."""

from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqResponseMessage
from openjiuwen.core.runner.drunner.dmessage_queue.message_serializer import (
    deserialize_message,
    serialize_message,
)


def test_reserved_marker_keys_survive_round_trip() -> None:
    payload = {
        "__class__": "not-a-registered-class",
        "__type__": "datetime",
        "value": "not-a-date",
        "__dsh_escaped__x": 1,
        "nested": [{"__class__": "tool-result"}],
    }

    restored = deserialize_message(serialize_message(DmqResponseMessage(payload=payload)))

    assert restored.payload == payload


def test_non_string_dict_keys_do_not_break_escaping() -> None:
    data = serialize_message(DmqResponseMessage(payload={1: "one"}))

    assert '"1"' in data.decode("utf-8")
