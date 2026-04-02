# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for HandoffSignal, extract_handoff_signal, and _find_handoff_payload.

Coverage:
1. HandoffSignal -- frozen dataclass field access and immutability
2. extract_handoff_signal -- direct dict, nested under output/result/content,
   missing key, empty target, non-string target, optional fields
3. _find_handoff_payload -- internal search logic via public interface
"""
from __future__ import annotations

import pytest

from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
    HANDOFF_MESSAGE_KEY,
    HANDOFF_REASON_KEY,
    HANDOFF_TARGET_KEY,
    HandoffSignal,
    extract_handoff_signal,
)


# ---------------------------------------------------------------------------
# 1. HandoffSignal
# ---------------------------------------------------------------------------

class TestHandoffSignal:
    @staticmethod
    def test_target_stored():
        sig = HandoffSignal(target="agent_b")
        assert sig.target == "agent_b"

    @staticmethod
    def test_message_defaults_to_none():
        assert HandoffSignal(target="b").message is None

    @staticmethod
    def test_reason_defaults_to_none():
        assert HandoffSignal(target="b").reason is None

    @staticmethod
    def test_custom_message():
        sig = HandoffSignal(target="b", message="context")
        assert sig.message == "context"

    @staticmethod
    def test_custom_reason():
        sig = HandoffSignal(target="b", reason="needs billing")
        assert sig.reason == "needs billing"

    @staticmethod
    def test_frozen_prevents_target_mutation():
        sig = HandoffSignal(target="b")
        with pytest.raises((AttributeError, TypeError)):
            sig.target = "x"  # type: ignore[misc]

    @staticmethod
    def test_equality_based_on_values():
        s1 = HandoffSignal(target="b", message="m", reason="r")
        s2 = HandoffSignal(target="b", message="m", reason="r")
        assert s1 == s2

    @staticmethod
    def test_inequality_different_target():
        assert HandoffSignal(target="a") != HandoffSignal(target="b")


# ---------------------------------------------------------------------------
# 2. extract_handoff_signal
# ---------------------------------------------------------------------------

class TestExtractHandoffSignal:
    @staticmethod
    def test_direct_dict_with_target():
        result = {HANDOFF_TARGET_KEY: "b"}
        sig = extract_handoff_signal(result)
        assert isinstance(sig, HandoffSignal)
        assert sig.target == "b"

    @staticmethod
    def test_direct_dict_with_reason():
        result = {HANDOFF_TARGET_KEY: "b", HANDOFF_REASON_KEY: "needs billing"}
        sig = extract_handoff_signal(result)
        assert sig.reason == "needs billing"

    @staticmethod
    def test_direct_dict_with_message():
        result = {HANDOFF_TARGET_KEY: "b", HANDOFF_MESSAGE_KEY: "carry this"}
        sig = extract_handoff_signal(result)
        assert sig.message == "carry this"

    @staticmethod
    def test_nested_under_output_key():
        result = {"output": {HANDOFF_TARGET_KEY: "c", HANDOFF_MESSAGE_KEY: "ctx"}}
        sig = extract_handoff_signal(result)
        assert sig is not None
        assert sig.target == "c"
        assert sig.message == "ctx"

    @staticmethod
    def test_nested_under_result_key():
        result = {"result": {HANDOFF_TARGET_KEY: "d"}}
        sig = extract_handoff_signal(result)
        assert sig is not None
        assert sig.target == "d"

    @staticmethod
    def test_nested_under_content_key():
        result = {"content": {HANDOFF_TARGET_KEY: "e"}}
        sig = extract_handoff_signal(result)
        assert sig is not None
        assert sig.target == "e"

    @staticmethod
    def test_no_handoff_key_returns_none():
        assert extract_handoff_signal({"result_type": "answer"}) is None

    @staticmethod
    def test_empty_dict_returns_none():
        assert extract_handoff_signal({}) is None

    @staticmethod
    def test_none_input_returns_none():
        assert extract_handoff_signal(None) is None

    @staticmethod
    def test_string_input_returns_none():
        assert extract_handoff_signal("plain string") is None

    @staticmethod
    def test_list_input_returns_none():
        assert extract_handoff_signal([{HANDOFF_TARGET_KEY: "b"}]) is None

    @staticmethod
    def test_int_input_returns_none():
        assert extract_handoff_signal(42) is None

    @staticmethod
    def test_empty_target_string_returns_none():
        assert extract_handoff_signal({HANDOFF_TARGET_KEY: ""}) is None

    @staticmethod
    def test_non_string_target_int_returns_none():
        assert extract_handoff_signal({HANDOFF_TARGET_KEY: 123}) is None

    @staticmethod
    def test_non_string_target_none_returns_none():
        assert extract_handoff_signal({HANDOFF_TARGET_KEY: None}) is None

    @staticmethod
    def test_non_string_target_list_returns_none():
        assert extract_handoff_signal({HANDOFF_TARGET_KEY: ["agent"]}) is None

    @staticmethod
    def test_message_none_when_key_absent():
        sig = extract_handoff_signal({HANDOFF_TARGET_KEY: "b"})
        assert sig.message is None

    @staticmethod
    def test_reason_none_when_key_absent():
        sig = extract_handoff_signal({HANDOFF_TARGET_KEY: "b"})
        assert sig.reason is None

    @staticmethod
    def test_message_none_when_empty_string():
        result = {HANDOFF_TARGET_KEY: "b", HANDOFF_MESSAGE_KEY: ""}
        sig = extract_handoff_signal(result)
        assert sig.message is None

    @staticmethod
    def test_reason_none_when_empty_string():
        result = {HANDOFF_TARGET_KEY: "b", HANDOFF_REASON_KEY: ""}
        sig = extract_handoff_signal(result)
        assert sig.reason is None

    @staticmethod
    def test_all_fields_populated():
        result = {
            HANDOFF_TARGET_KEY: "agent_x",
            HANDOFF_MESSAGE_KEY: "context data",
            HANDOFF_REASON_KEY: "specialist needed",
        }
        sig = extract_handoff_signal(result)
        assert sig.target == "agent_x"
        assert sig.message == "context data"
        assert sig.reason == "specialist needed"

    @staticmethod
    def test_direct_key_takes_priority_over_nested():
        result = {
            HANDOFF_TARGET_KEY: "direct_agent",
            "output": {HANDOFF_TARGET_KEY: "nested_agent"},
        }
        sig = extract_handoff_signal(result)
        assert sig.target == "direct_agent"

    @staticmethod
    def test_nested_output_non_dict_ignored():
        result = {"output": "not a dict"}
        assert extract_handoff_signal(result) is None

    @staticmethod
    def test_nested_result_non_dict_ignored():
        result = {"result": 42}
        assert extract_handoff_signal(result) is None

    @staticmethod
    def test_nested_content_non_dict_ignored():
        result = {"content": ["list"]}
        assert extract_handoff_signal(result) is None

    @staticmethod
    def test_constant_target_key_value():
        assert HANDOFF_TARGET_KEY == "__handoff_to__"

    @staticmethod
    def test_constant_message_key_value():
        assert HANDOFF_MESSAGE_KEY == "__handoff_message__"

    @staticmethod
    def test_constant_reason_key_value():
        assert HANDOFF_REASON_KEY == "__handoff_reason__"
