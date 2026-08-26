# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.extensions.app.models."""

from openjiuwen.extensions.app.models import Envelope, make_envelope


class TestEnvelope:
    def test_defaults_are_generated(self):
        envelope = Envelope(type="chat.token")
        assert envelope.id
        assert envelope.timestamp > 0
        assert envelope.conversationId is None
        assert envelope.payload == {}

    def test_explicit_fields_are_kept(self):
        envelope = Envelope(type="genui", conversationId="conv-1", payload={"a": 1})
        assert envelope.type == "genui"
        assert envelope.conversationId == "conv-1"
        assert envelope.payload == {"a": 1}


class TestMakeEnvelope:
    def test_builds_envelope_with_payload_and_conversation_id(self):
        envelope = make_envelope("chat.accepted", {"text": "hi"}, "conv-42")
        assert envelope.type == "chat.accepted"
        assert envelope.conversationId == "conv-42"
        assert envelope.payload == {"text": "hi"}

    def test_defaults_payload_to_empty_dict(self):
        envelope = make_envelope("chat.completed")
        assert envelope.payload == {}
        assert envelope.conversationId is None
