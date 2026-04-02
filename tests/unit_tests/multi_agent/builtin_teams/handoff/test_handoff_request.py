# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for HandoffRequest.

Coverage:
1. Construction -- defaults, custom fields
2. session_id property -- no session, with session
3. History field isolation per instance
"""
from __future__ import annotations

from unittest.mock import MagicMock

from openjiuwen.core.multi_agent.teams.handoff.handoff_request import HandoffRequest


# ---------------------------------------------------------------------------
# 1. Construction
# ---------------------------------------------------------------------------

class TestHandoffRequestConstruction:
    @staticmethod
    def test_input_message_stored():
        req = HandoffRequest(input_message="hello")
        assert req.input_message == "hello"

    @staticmethod
    def test_dict_input_message_stored():
        msg = {"query": "what is 2+2"}
        req = HandoffRequest(input_message=msg)
        assert req.input_message is msg

    @staticmethod
    def test_default_history_is_empty_list():
        req = HandoffRequest(input_message="x")
        assert req.history == []

    @staticmethod
    def test_default_session_is_none():
        req = HandoffRequest(input_message="x")
        assert req.session is None

    @staticmethod
    def test_custom_history_stored():
        hist = [{"agent": "a", "output": {"ok": True}}]
        req = HandoffRequest(input_message="x", history=hist)
        assert req.history == hist

    @staticmethod
    def test_custom_session_stored():
        session = MagicMock()
        req = HandoffRequest(input_message="x", session=session)
        assert req.session is session


# ---------------------------------------------------------------------------
# 2. session_id property
# ---------------------------------------------------------------------------

class TestHandoffRequestSessionId:
    @staticmethod
    def test_session_id_empty_string_when_no_session():
        req = HandoffRequest(input_message="hello")
        assert req.session_id == ""

    @staticmethod
    def test_session_id_from_session():
        session = MagicMock()
        session.get_session_id = MagicMock(return_value="sid-123")
        req = HandoffRequest(input_message="hi", session=session)
        assert req.session_id == "sid-123"

    @staticmethod
    def test_session_id_calls_get_session_id_once():
        session = MagicMock()
        session.get_session_id = MagicMock(return_value="abc")
        req = HandoffRequest(input_message="hi", session=session)
        _ = req.session_id
        session.get_session_id.assert_called_once()

    @staticmethod
    def test_session_id_is_string_type():
        req = HandoffRequest(input_message="x")
        assert isinstance(req.session_id, str)

    @staticmethod
    def test_session_id_changes_with_session():
        session = MagicMock()
        session.get_session_id = MagicMock(return_value="new-id")
        req = HandoffRequest(input_message="x", session=session)
        assert req.session_id == "new-id"


# ---------------------------------------------------------------------------
# 3. History field isolation per instance
# ---------------------------------------------------------------------------

class TestHandoffRequestHistoryIsolation:
    @staticmethod
    def test_default_history_not_shared_across_instances():
        req1 = HandoffRequest(input_message="a")
        req2 = HandoffRequest(input_message="b")
        req1.history.append({"agent": "x", "output": {}})
        assert req2.history == []

    @staticmethod
    def test_history_mutability():
        req = HandoffRequest(input_message="x")
        req.history.append({"agent": "a", "output": {}})
        assert len(req.history) == 1

    @staticmethod
    def test_history_length_matches_supplied():
        hist = [{"agent": f"agent_{i}", "output": {}} for i in range(5)]
        req = HandoffRequest(input_message="x", history=hist)
        assert len(req.history) == 5
