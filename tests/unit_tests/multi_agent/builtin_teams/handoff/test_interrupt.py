# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for TeamInterruptSignal, extract_interrupt_signal, and flush_team_session.

Coverage:
1. TeamInterruptSignal -- construction, fields
2. extract_interrupt_signal -- interrupt result, non-interrupt result, AgentInterrupt exc,
   non-AgentInterrupt exc, None inputs, result takes priority over exc
3. flush_team_session -- None guard, post_run called, exception swallowed, warning logged
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.teams.handoff.interrupt import (
    TeamInterruptSignal,
    extract_interrupt_signal,
    flush_team_session,
)


class TestTeamInterruptSignal:
    @staticmethod
    def test_result_stored():
        payload = {"result_type": "interrupt"}
        sig = TeamInterruptSignal(result=payload)
        assert sig.result is payload

    @staticmethod
    def test_message_defaults_to_none():
        assert TeamInterruptSignal(result={}).message is None

    @staticmethod
    def test_custom_message():
        sig = TeamInterruptSignal(result={}, message="paused")
        assert sig.message == "paused"

    @staticmethod
    def test_result_type_preserved():
        payload = {"result_type": "interrupt", "data": 42}
        sig = TeamInterruptSignal(result=payload)
        assert sig.result["result_type"] == "interrupt"
        assert sig.result["data"] == 42


class TestExtractInterruptSignal:
    @staticmethod
    def test_interrupt_result_returns_signal():
        result = {"result_type": "interrupt", "message": "need input"}
        sig = extract_interrupt_signal(result=result)
        assert isinstance(sig, TeamInterruptSignal)
        assert sig.result is result

    @staticmethod
    def test_non_interrupt_result_returns_none():
        assert extract_interrupt_signal(result={"result_type": "answer"}) is None

    @staticmethod
    def test_non_dict_result_returns_none():
        assert extract_interrupt_signal(result="interrupt") is None

    @staticmethod
    def test_none_result_returns_none():
        assert extract_interrupt_signal(result=None) is None

    @staticmethod
    def test_both_none_returns_none():
        assert extract_interrupt_signal() is None

    @staticmethod
    def test_agent_interrupt_exc_returns_signal():
        from openjiuwen.core.session.interaction.base import AgentInterrupt
        exc = AgentInterrupt(message="waiting for user")
        sig = extract_interrupt_signal(exc=exc)
        assert isinstance(sig, TeamInterruptSignal)
        assert sig.message == "waiting for user"
        assert sig.result["result_type"] == "interrupt"

    @staticmethod
    def test_non_agent_interrupt_exc_returns_none():
        assert extract_interrupt_signal(exc=ValueError("err")) is None

    @staticmethod
    def test_interrupt_result_takes_priority_over_exc():
        from openjiuwen.core.session.interaction.base import AgentInterrupt
        result = {"result_type": "interrupt"}
        exc = AgentInterrupt(message="from exc")
        sig = extract_interrupt_signal(result=result, exc=exc)
        assert sig.result is result

    @staticmethod
    def test_non_interrupt_result_falls_through_to_exc():
        from openjiuwen.core.session.interaction.base import AgentInterrupt
        result = {"result_type": "answer"}
        exc = AgentInterrupt(message="from exc")
        sig = extract_interrupt_signal(result=result, exc=exc)
        assert isinstance(sig, TeamInterruptSignal)
        assert sig.message == "from exc"

    @staticmethod
    def test_agent_interrupt_result_message_in_payload():
        from openjiuwen.core.session.interaction.base import AgentInterrupt
        exc = AgentInterrupt(message="pause reason")
        sig = extract_interrupt_signal(exc=exc)
        assert sig.result["message"] == "pause reason"

    @staticmethod
    def test_list_result_returns_none():
        assert extract_interrupt_signal(result=[{"result_type": "interrupt"}]) is None

    @staticmethod
    def test_missing_result_type_returns_none():
        assert extract_interrupt_signal(result={"data": "x"}) is None


class TestFlushTeamSession:
    @pytest.mark.asyncio
    async def test_none_session_returns_silently(self):
        await flush_team_session(None)

    @pytest.mark.asyncio
    async def test_none_session_does_not_call_post_run(self):
        session = MagicMock()
        session.post_run = AsyncMock()
        await flush_team_session(None)
        session.post_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_post_run_called_once(self):
        session = MagicMock()
        session.post_run = AsyncMock(return_value=None)
        await flush_team_session(session)
        session.post_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        session = MagicMock()
        session.post_run = AsyncMock(side_effect=RuntimeError("checkpointer down"))
        await flush_team_session(session)

    @pytest.mark.asyncio
    async def test_warning_logged_on_failure(self):
        session = MagicMock()
        session.post_run = AsyncMock(side_effect=OSError("storage unavailable"))
        with patch("openjiuwen.core.multi_agent.teams.handoff.interrupt.logger") as mock_logger:
            await flush_team_session(session)
            mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_warning_logged_with_exc_info(self):
        session = MagicMock()
        session.post_run = AsyncMock(side_effect=ConnectionError("redis timeout"))
        with patch("openjiuwen.core.multi_agent.teams.handoff.interrupt.logger") as mock_logger:
            await flush_team_session(session)
            _, kwargs = mock_logger.warning.call_args
            assert kwargs.get("exc_info") is True

    @pytest.mark.asyncio
    async def test_warning_message_contains_flush_or_checkpointer(self):
        session = MagicMock()
        session.post_run = AsyncMock(side_effect=RuntimeError("fail"))
        with patch("openjiuwen.core.multi_agent.teams.handoff.interrupt.logger") as mock_logger:
            await flush_team_session(session)
            args, _ = mock_logger.warning.call_args
            combined = " ".join(str(a) for a in args).lower()
            assert "flush" in combined or "checkpointer" in combined

    @pytest.mark.asyncio
    async def test_no_warning_on_success(self):
        session = MagicMock()
        session.post_run = AsyncMock(return_value=None)
        with patch("openjiuwen.core.multi_agent.teams.handoff.interrupt.logger") as mock_logger:
            await flush_team_session(session)
            mock_logger.warning.assert_not_called()
