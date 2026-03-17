# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for MessageEnvelope."""
import pytest

from openjiuwen.core.multi_agent.group_runtime.envelope import MessageEnvelope


class TestMessageEnvelope:
    """Tests for MessageEnvelope dataclass."""

    @staticmethod
    def test_create_p2p_envelope():
        """MessageEnvelope with recipient is a P2P message."""
        envelope = MessageEnvelope(
            message_id="msg-001",
            message="hello",
            sender="agent_a",
            recipient="agent_b",
        )
        assert envelope.message_id == "msg-001"
        assert envelope.message == "hello"
        assert envelope.sender == "agent_a"
        assert envelope.recipient == "agent_b"
        assert envelope.topic_id is None
        assert envelope.session_id is None
        assert envelope.metadata == {}

    @staticmethod
    def test_create_pubsub_envelope():
        """MessageEnvelope with topic_id is a Pub-Sub message."""
        envelope = MessageEnvelope(
            message_id="msg-002",
            message={"event": "done"},
            sender="agent_a",
            topic_id="code_events",
        )
        assert envelope.topic_id == "code_events"
        assert envelope.recipient is None

    @staticmethod
    def test_is_p2p_true_when_recipient_set():
        envelope = MessageEnvelope(
            message_id="x",
            message="payload",
            recipient="agent_b",
        )
        assert envelope.is_p2p() is True
        assert envelope.is_pubsub() is False

    @staticmethod
    def test_is_pubsub_true_when_topic_set():
        envelope = MessageEnvelope(
            message_id="y",
            message="payload",
            topic_id="events",
        )
        assert envelope.is_pubsub() is True
        assert envelope.is_p2p() is False

    @staticmethod
    def test_both_flags_can_be_false():
        """Envelope with neither recipient nor topic_id."""
        envelope = MessageEnvelope(
            message_id="z",
            message="payload",
        )
        assert envelope.is_p2p() is False
        assert envelope.is_pubsub() is False

    @staticmethod
    def test_envelope_is_frozen():
        """MessageEnvelope is frozen - mutation should raise."""
        envelope = MessageEnvelope(message_id="id", message="data")
        with pytest.raises((AttributeError, TypeError)):
            envelope.message_id = "new-id"  # type: ignore[misc]

    @staticmethod
    def test_metadata_default_is_empty_dict():
        envelope = MessageEnvelope(message_id="id", message="data")
        assert envelope.metadata == {}
        # Each instance gets its own dict (not shared)
        envelope2 = MessageEnvelope(message_id="id2", message="data2")
        assert envelope.metadata is not envelope2.metadata

    @staticmethod
    def test_repr_contains_key_fields():
        envelope = MessageEnvelope(
            message_id="repr-test",
            message="payload",
            sender="alice",
            recipient="bob",
        )
        r = repr(envelope)
        assert "repr-test" in r
        assert "alice" in r
        assert "bob" in r

    @staticmethod
    def test_session_id_stored():
        envelope = MessageEnvelope(
            message_id="s1",
            message="data",
            session_id="session-xyz",
        )
        assert envelope.session_id == "session-xyz"

    @staticmethod
    def test_message_can_be_any_type():
        """message field accepts any Python object."""
        for payload in [42, [1, 2], {"a": 1}, None, object()]:
            env = MessageEnvelope(message_id="id", message=payload)
            assert env.message is payload

    @staticmethod
    def test_metadata_custom():
        """Custom metadata dict is preserved."""
        meta = {"priority": "high", "retry": 3}
        envelope = MessageEnvelope(
            message_id="m1",
            message="x",
            metadata=meta,
        )
        assert envelope.metadata == meta
