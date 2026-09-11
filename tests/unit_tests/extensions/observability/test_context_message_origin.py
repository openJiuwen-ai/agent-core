# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.llm import (
    OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
    OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
    OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA,
    SystemMessage,
    UserMessage,
)
from openjiuwen.extensions.observability.callback_handler import (
    OtelCallbackHandler,
    _trajectory_message_origin,
)
from openjiuwen.extensions.observability.config import ObservabilityConfig


def test_explicit_harness_input_is_external_user_with_source_kind() -> None:
    message = UserMessage(
        content="hello",
        metadata={
            OPENJIUWEN_MESSAGE_ORIGIN_METADATA: OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
            OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA: "next-turn",
        },
    )

    assert _trajectory_message_origin(message) == {
        "origin": "external_user",
        "source_kind": "next-turn",
    }


def test_unmarked_harness_generated_user_is_internal_without_content_guessing() -> None:
    message = UserMessage(content="<memory_block_current>same user text</memory_block_current>")

    assert _trajectory_message_origin(message) == {
        "origin": "harness_internal",
    }


def test_non_user_role_cannot_claim_external_user_origin() -> None:
    message = SystemMessage(
        content="system",
        metadata={
            OPENJIUWEN_MESSAGE_ORIGIN_METADATA: OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
            OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA: "query",
        },
    )

    assert _trajectory_message_origin(message) == {
        "origin": "harness_internal",
    }


def test_provider_metadata_loss_uses_the_captured_source_origin() -> None:
    provider_message = UserMessage(
        content="external",
        metadata={"context_message_id": "message-1"},
    )
    source_metadata = {
        OPENJIUWEN_MESSAGE_ORIGIN_METADATA: OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
        OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA: "query",
    }

    assert _trajectory_message_origin(provider_message, source_metadata) == {
        "origin": "external_user",
        "source_kind": "query",
    }


def test_canonical_trajectory_messages_persist_explicit_origin_fields() -> None:
    external = UserMessage(
        content="external",
        metadata={
            OPENJIUWEN_MESSAGE_ORIGIN_METADATA: OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER,
            OPENJIUWEN_MESSAGE_SOURCE_KIND_METADATA: "query",
        },
    )
    internal = UserMessage(content="internal")
    handler = OtelCallbackHandler(ObservabilityConfig(enabled=True))

    messages = handler._trajectory_messages(
        [external, internal],
        occurrence_ids=("external-id", "internal-id"),
        source_metadata=(external.metadata, internal.metadata),
    )

    assert messages[0]["origin"] == "external_user"
    assert messages[0]["source_kind"] == "query"
    assert messages[1]["origin"] == "harness_internal"
    assert "source_kind" not in messages[1]
