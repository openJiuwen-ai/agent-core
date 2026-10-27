# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression: checkpoint-restored AssistantMessage may lack newer optional fields."""

from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


def _strip_optional_fields(msg: AssistantMessage, *fields: str) -> AssistantMessage:
    """Simulate an old checkpoint instance whose __dict__ predates newer schema keys."""
    for field in fields:
        msg.__dict__.pop(field, None)
    return msg


def test_model_dump_tolerates_missing_response_id_from_old_checkpoint() -> None:
    msg = _strip_optional_fields(
        AssistantMessage(content="hello from restored session"),
        "response_id",
        "response_model",
        "provider_metadata",
        "provider_content",
        "prompt_token_ids",
        "completion_token_ids",
        "logprobs",
    )

    dumped = msg.model_dump()

    assert dumped["role"] == "assistant"
    assert dumped["content"] == "hello from restored session"
    assert "response_id" not in dumped
    assert "response_model" not in dumped
    assert "provider_metadata" not in dumped


def test_model_dump_tolerates_missing_tool_call_response_item_id() -> None:
    call = ToolCall(id="tc-1", type="function", name="echo", arguments="{}")
    call.__dict__.pop("response_item_id", None)
    msg = AssistantMessage(content="", tool_calls=[call])

    dumped = msg.model_dump()

    assert len(dumped["tool_calls"]) == 1
    assert "response_item_id" not in dumped["tool_calls"][0]


def test_model_dump_still_emits_present_optional_fields() -> None:
    msg = AssistantMessage(
        content="ok",
        response_id="resp-1",
        response_model="gpt-test",
        provider_metadata={"trace": "x"},
    )

    dumped = msg.model_dump()

    assert dumped["response_id"] == "resp-1"
    assert dumped["response_model"] == "gpt-test"
    assert dumped["provider_metadata"] == {"trace": "x"}
