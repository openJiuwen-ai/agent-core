# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for HandoffTool.

Coverage:
1. Construction -- card name, description, input_params schema
2. invoke() -- dict input, JSON string input, plain string fallback, empty dict,
   non-dict non-string input, missing optional fields
3. stream() -- yields single invoke result
"""
from __future__ import annotations

import json

import pytest

from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
    HANDOFF_MESSAGE_KEY,
    HANDOFF_REASON_KEY,
    HANDOFF_TARGET_KEY,
)
from openjiuwen.core.multi_agent.teams.handoff.handoff_tool import HandoffTool
from openjiuwen.core.foundation.tool import Tool


# ---------------------------------------------------------------------------
# 1. Construction
# ---------------------------------------------------------------------------

class TestHandoffToolConstruction:
    @staticmethod
    def test_is_tool_subclass():
        assert isinstance(HandoffTool(target_id="b"), Tool)

    @staticmethod
    def test_card_name_prefixed_with_transfer_to():
        tool = HandoffTool(target_id="agent_b")
        assert tool.card.name == "transfer_to_agent_b"

    @staticmethod
    def test_card_id_matches_name():
        tool = HandoffTool(target_id="agent_b")
        assert tool.card.id == "transfer_to_agent_b"

    @staticmethod
    def test_description_contains_target_id():
        tool = HandoffTool(target_id="billing_agent")
        assert "billing_agent" in tool.card.description

    @staticmethod
    def test_description_appends_target_description():
        tool = HandoffTool(target_id="b", target_description="handles billing")
        assert "handles billing" in tool.card.description

    @staticmethod
    def test_description_without_target_description_non_empty():
        tool = HandoffTool(target_id="b", target_description="")
        assert len(tool.card.description) > 0

    @staticmethod
    def test_input_params_schema_type_object():
        tool = HandoffTool(target_id="b")
        assert tool.card.input_params["type"] == "object"

    @staticmethod
    def test_input_params_reason_is_required():
        tool = HandoffTool(target_id="b")
        required = tool.card.input_params.get("required", [])
        assert "reason" in required

    @staticmethod
    def test_input_params_message_not_required():
        tool = HandoffTool(target_id="b")
        required = tool.card.input_params.get("required", [])
        assert "message" not in required

    @staticmethod
    def test_input_params_reason_property_type():
        tool = HandoffTool(target_id="b")
        props = tool.card.input_params["properties"]
        assert props["reason"]["type"] == "string"

    @staticmethod
    def test_input_params_message_property_type():
        tool = HandoffTool(target_id="b")
        props = tool.card.input_params["properties"]
        assert props["message"]["type"] == "string"


# ---------------------------------------------------------------------------
# 2. invoke()
# ---------------------------------------------------------------------------

class TestHandoffToolInvoke:
    @staticmethod
    @pytest.mark.asyncio
    async def test_dict_input_target_key():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({"reason": "go", "message": "ctx"})
        assert result[HANDOFF_TARGET_KEY] == "b"

    @staticmethod
    @pytest.mark.asyncio
    async def test_dict_input_reason_key():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({"reason": "need billing", "message": ""})
        assert result[HANDOFF_REASON_KEY] == "need billing"

    @staticmethod
    @pytest.mark.asyncio
    async def test_dict_input_message_key():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({"reason": "r", "message": "carry this"})
        assert result[HANDOFF_MESSAGE_KEY] == "carry this"

    @staticmethod
    @pytest.mark.asyncio
    async def test_json_string_input_target():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke(json.dumps({"reason": "go", "message": "hi"}))
        assert result[HANDOFF_TARGET_KEY] == "b"

    @staticmethod
    @pytest.mark.asyncio
    async def test_json_string_input_reason():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke(json.dumps({"reason": "json reason"}))
        assert result[HANDOFF_REASON_KEY] == "json reason"

    @staticmethod
    @pytest.mark.asyncio
    async def test_plain_string_fallback_target():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke("plain fallback reason")
        assert result[HANDOFF_TARGET_KEY] == "b"

    @staticmethod
    @pytest.mark.asyncio
    async def test_plain_string_fallback_reason():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke("plain fallback reason")
        assert result[HANDOFF_REASON_KEY] == "plain fallback reason"

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_dict_input_reason_empty():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({})
        assert result[HANDOFF_REASON_KEY] == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_dict_input_message_empty():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({})
        assert result[HANDOFF_MESSAGE_KEY] == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_none_input_treated_as_empty_dict():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke(None)
        assert result[HANDOFF_TARGET_KEY] == "b"
        assert result[HANDOFF_REASON_KEY] == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_list_input_treated_as_empty_dict():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke(["not", "a", "dict"])
        assert result[HANDOFF_TARGET_KEY] == "b"

    @staticmethod
    @pytest.mark.asyncio
    async def test_missing_message_defaults_to_empty_string():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({"reason": "only reason"})
        assert result[HANDOFF_MESSAGE_KEY] == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_none_message_defaults_to_empty_string():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({"reason": "r", "message": None})
        assert result[HANDOFF_MESSAGE_KEY] == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_none_reason_defaults_to_empty_string():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({"reason": None})
        assert result[HANDOFF_REASON_KEY] == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_result_has_all_three_keys():
        tool = HandoffTool(target_id="b")
        result = await tool.invoke({"reason": "r"})
        assert HANDOFF_TARGET_KEY in result
        assert HANDOFF_MESSAGE_KEY in result
        assert HANDOFF_REASON_KEY in result

    @staticmethod
    @pytest.mark.asyncio
    async def test_different_target_ids_produce_correct_target():
        for tid in ["agent_x", "billing", "support_123"]:
            tool = HandoffTool(target_id=tid)
            result = await tool.invoke({"reason": "go"})
            assert result[HANDOFF_TARGET_KEY] == tid


# ---------------------------------------------------------------------------
# 3. stream()
# ---------------------------------------------------------------------------

class TestHandoffToolStream:
    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_yields_exactly_one_chunk():
        tool = HandoffTool(target_id="b")
        chunks = [c async for c in tool.stream({"reason": "r"})]
        assert len(chunks) == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_chunk_has_target_key():
        tool = HandoffTool(target_id="b")
        chunks = [c async for c in tool.stream({"reason": "r"})]
        assert chunks[0][HANDOFF_TARGET_KEY] == "b"

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_chunk_matches_invoke_result():
        tool = HandoffTool(target_id="b")
        invoke_result = await tool.invoke({"reason": "test", "message": "msg"})
        stream_chunks = [c async for c in tool.stream({"reason": "test", "message": "msg"})]
        assert stream_chunks[0] == invoke_result

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_with_empty_input():
        tool = HandoffTool(target_id="b")
        chunks = [c async for c in tool.stream({})]
        assert len(chunks) == 1
        assert chunks[0][HANDOFF_TARGET_KEY] == "b"
