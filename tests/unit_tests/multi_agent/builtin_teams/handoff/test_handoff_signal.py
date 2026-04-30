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


# ---------------------------------------------------------------------------
# 3. _find_handoff_from_session -- recovery from overwritten result
# ---------------------------------------------------------------------------

class _FakeMsg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _FakeSession:
    def __init__(self, ctx_state):
        self._ctx_state = ctx_state

    def get_state(self, key):
        return self._ctx_state if key == "context" else None


_DEFAULT_CONTEXT_ID = "default_context_id"


class TestFindHandoffFromSession:
    @staticmethod
    def test_finds_json_handoff_from_tool_message():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("user", "I need help"),
                    _FakeMsg(
                        "tool",
                        '{"__handoff_to__": "billing_agent", "__handoff_reason__": "billing question"}',
                    ),
                ],
                "offload_messages": {},
            }
        })
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        result = _find_handoff_from_session(session)
        assert result is not None
        assert result["__handoff_to__"] == "billing_agent"
        assert result["__handoff_reason__"] == "billing question"

    @staticmethod
    def test_finds_handoff_from_python_dict_repr():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg(
                        "tool",
                        "{'__handoff_to__': 'tech_agent', '__handoff_message__': 'escalate'}",
                    ),
                ],
                "offload_messages": {},
            }
        })
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        result = _find_handoff_from_session(session)
        assert result is not None
        assert result["__handoff_to__"] == "tech_agent"
        assert result["__handoff_message__"] == "escalate"

    @staticmethod
    def test_reversed_search_returns_last_handoff():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg(
                        "tool",
                        '{"__handoff_to__": "first_agent"}',
                    ),
                    _FakeMsg(
                        "tool",
                        '{"__handoff_to__": "second_agent"}',
                    ),
                ],
                "offload_messages": {},
            }
        })
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        result = _find_handoff_from_session(session)
        assert result["__handoff_to__"] == "second_agent"

    @staticmethod
    def test_non_tool_messages_ignored():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("assistant", '{"__handoff_to__": "billing_agent"}'),
                ],
                "offload_messages": {},
            }
        })
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None

    @staticmethod
    def test_returns_none_when_no_handoff_key():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("tool", '{"result_type": "answer"}'),
                ],
                "offload_messages": {},
            }
        })
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None

    @staticmethod
    def test_returns_none_for_unparseable_content():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("tool", "not valid json or python"),
                ],
                "offload_messages": {},
            }
        })
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None

    @staticmethod
    def test_returns_none_when_agent_session_is_none():
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(None) is None

    @staticmethod
    def test_returns_none_when_context_state_missing():
        session = _FakeSession(None)
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None

    @staticmethod
    def test_returns_none_when_context_state_not_dict():
        session = _FakeSession("not a dict")
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None

    @staticmethod
    def test_returns_none_when_no_messages():
        session = _FakeSession({_DEFAULT_CONTEXT_ID: {"messages": [], "offload_messages": {}}})
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None

    @staticmethod
    def test_returns_none_when_default_context_key_missing():
        session = _FakeSession({})
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None

    @staticmethod
    def test_empty_tool_content_ignored():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("tool", ""),
                ],
                "offload_messages": {},
            }
        })
        from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
            _find_handoff_from_session,
        )
        assert _find_handoff_from_session(session) is None


# ---------------------------------------------------------------------------
# 4. extract_handoff_signal with agent_session -- recovery path
# ---------------------------------------------------------------------------

class TestExtractHandoffSignalWithSession:
    @staticmethod
    def test_result_without_handoff_recovered_from_session():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("tool", '{"__handoff_to__": "recovered_agent"}'),
                ],
                "offload_messages": {},
            }
        })
        sig = extract_handoff_signal({"output": "plain answer"}, agent_session=session)
        assert sig is not None
        assert sig.target == "recovered_agent"

    @staticmethod
    def test_result_handoff_takes_priority_over_session():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("tool", '{"__handoff_to__": "session_agent"}'),
                ],
                "offload_messages": {},
            }
        })
        result = {HANDOFF_TARGET_KEY: "result_agent"}
        sig = extract_handoff_signal(result, agent_session=session)
        assert sig is not None
        assert sig.target == "result_agent"

    @staticmethod
    def test_no_handoff_in_result_or_session_returns_none():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg("tool", '{"result_type": "answer"}'),
                ],
                "offload_messages": {},
            }
        })
        assert extract_handoff_signal({"output": "hello"}, agent_session=session) is None

    @staticmethod
    def test_none_agent_session_falls_back_to_result_only():
        sig = extract_handoff_signal({HANDOFF_TARGET_KEY: "direct_agent"})
        assert sig is not None
        assert sig.target == "direct_agent"

    @staticmethod
    def test_session_recovery_supplies_optional_fields():
        session = _FakeSession({
            _DEFAULT_CONTEXT_ID: {
                "messages": [
                    _FakeMsg(
                        "tool",
                        '{"__handoff_to__": "specialist", '
                        '"__handoff_message__": "urgent", '
                        '"__handoff_reason__": "complex issue"}',
                    ),
                ],
                "offload_messages": {},
            }
        })
        sig = extract_handoff_signal({"output": "ignored"}, agent_session=session)
        assert sig.target == "specialist"
        assert sig.message == "urgent"
        assert sig.reason == "complex issue"
