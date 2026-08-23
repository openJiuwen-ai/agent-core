# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the DB-mailbox approval fallback in ``MessageHandler``.

The event path (``AgentLifecycleHandler.on_tool_approval_result``) builds an
``InteractiveInput`` and hands it to ``resume_interrupt``; the DB mailbox path
used to hand the raw parsed dict, which ``is_pending_interrupt_resume_valid``
silently drops (``isinstance`` guard). Task 7 extracts a
``_approval_to_interactive_input`` helper that mirrors the event-path
construction, and adds an idempotency guard so a DB poll arriving after the
event already cleared the interrupt does not re-resume.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


def _msg(d: dict, *, message_id: str = "m1", from_member_name: str = "leader") -> MagicMock:
    """Minimal mailbox row carrying a JSON approval payload."""
    m = MagicMock()
    m.protocol = "json"
    m.content = json.dumps(d)
    m.message_id = message_id
    m.from_member_name = from_member_name
    m.coordination_meta = None
    return m


def test_approval_to_interactive_input_constructs_ii() -> None:
    ii = MessageHandler._approval_to_interactive_input(
        _msg(
            {
                "type": "tool_approval_result",
                "tool_call_id": "tcid-1",
                "approved": True,
                "feedback": "ok",
                "auto_confirm": False,
            }
        )
    )
    assert isinstance(ii, InteractiveInput)
    assert "tcid-1" in ii.user_inputs
    assert ii.user_inputs["tcid-1"]["approved"] is True
    assert ii.user_inputs["tcid-1"]["feedback"] == "ok"
    assert ii.user_inputs["tcid-1"]["auto_confirm"] is False


def test_approval_to_interactive_input_none_for_non_approval() -> None:
    assert MessageHandler._approval_to_interactive_input(_msg({"type": "other"})) is None
    assert MessageHandler._approval_to_interactive_input(_msg({"type": "tool_approval_result"})) is None


@pytest.mark.asyncio
async def test_db_resume_skipped_when_event_already_cleared() -> None:
    """Idempotency guard: a DB poll after the event cleared the interrupt must not re-resume."""
    handler = MessageHandler.__new__(MessageHandler)
    handler._blueprint = MagicMock()
    handler._blueprint.role = TeamRole.TEAMMATE
    handler._round = MagicMock(spec=TeamAgent)
    handler._round.has_pending_interrupt = MagicMock(return_value=True)
    # Event path already cleared the interrupt -> resume is no longer valid.
    handler._round.is_pending_interrupt_resume_valid = lambda ii: False
    handler._round.resume_interrupt = AsyncMock()
    handler._infra = MagicMock()
    handler._infra.message_manager = MagicMock()
    handler._infra.message_manager.mark_message_read = AsyncMock()
    handler._infra.team_backend.debate_state = None
    handler._infra.team_backend.is_human_agent = AsyncMock(return_value=False)
    handler._harness_input_blocked = AsyncMock(return_value=False)
    handler._read_all_unread = AsyncMock(
        return_value=[
            _msg(
                {
                    "type": "tool_approval_result",
                    "tool_call_id": "tcid-1",
                    "approved": True,
                },
                message_id="m1",
            )
        ]
    )

    await handler._process_unread_messages("teammate-1")

    # The approval was parsed and marked read, but resume was NOT called —
    # the event path had already cleared the pending interrupt.
    handler._round.resume_interrupt.assert_not_awaited()
    handler._infra.message_manager.mark_message_read.assert_awaited_once_with("m1", "teammate-1")


@pytest.mark.asyncio
async def test_db_resume_delivered_when_interrupt_still_pending() -> None:
    """Positive complement: when the interrupt is still pending, the DB path delivers the II."""
    handler = MessageHandler.__new__(MessageHandler)
    handler._blueprint = MagicMock()
    handler._blueprint.role = TeamRole.TEAMMATE
    handler._round = MagicMock(spec=TeamAgent)
    handler._round.has_pending_interrupt = MagicMock(return_value=True)
    handler._round.is_pending_interrupt_resume_valid = lambda ii: True
    handler._round.resume_interrupt = AsyncMock()
    handler._infra = MagicMock()
    handler._infra.message_manager = MagicMock()
    handler._infra.message_manager.mark_message_read = AsyncMock()
    handler._infra.team_backend.debate_state = None
    handler._infra.team_backend.is_human_agent = AsyncMock(return_value=False)
    handler._harness_input_blocked = AsyncMock(return_value=False)
    handler._read_all_unread = AsyncMock(
        return_value=[
            _msg(
                {
                    "type": "tool_approval_result",
                    "tool_call_id": "tcid-1",
                    "approved": True,
                },
                message_id="m1",
            )
        ]
    )

    await handler._process_unread_messages("teammate-1")

    handler._round.resume_interrupt.assert_awaited_once()
    delivered = handler._round.resume_interrupt.await_args.args[0]
    assert isinstance(delivered, InteractiveInput)
    assert "tcid-1" in delivered.user_inputs


@pytest.mark.asyncio
async def test_db_resume_valid_reaches_teamagent_method_without_attribute_error() -> None:
    """C1: ``self._round`` is the owning TeamAgent (handlers/base.py aliases
    the host under ``_round``), so the DB approval path's call to
    ``is_pending_interrupt_resume_valid`` must resolve on TeamAgent, which
    delegates to the stream controller. A spec'd ``MagicMock(spec=TeamAgent)``
    only allows attributes that exist on TeamAgent — before the C1 fix this
    method did not exist, so even *setting* it raised AttributeError and the
    DB poll silently dropped the approval after ``mark_message_read``. This
    pins the delegation: remove the TeamAgent method and the spec'd mock
    rejects the attribute access here.
    """
    handler = MessageHandler.__new__(MessageHandler)
    handler._blueprint = MagicMock()
    handler._blueprint.role = TeamRole.TEAMMATE
    handler._round = MagicMock(spec=TeamAgent)
    handler._round.has_pending_interrupt = MagicMock(return_value=True)
    handler._round.is_pending_interrupt_resume_valid = MagicMock(return_value=True)
    handler._round.resume_interrupt = AsyncMock()
    handler._infra = MagicMock()
    handler._infra.message_manager = MagicMock()
    handler._infra.message_manager.mark_message_read = AsyncMock()
    handler._infra.team_backend.debate_state = None
    handler._infra.team_backend.is_human_agent = AsyncMock(return_value=False)
    handler._harness_input_blocked = AsyncMock(return_value=False)
    handler._read_all_unread = AsyncMock(
        return_value=[
            _msg(
                {
                    "type": "tool_approval_result",
                    "tool_call_id": "tcid-1",
                    "approved": True,
                },
                message_id="m1",
            )
        ]
    )

    await handler._process_unread_messages("teammate-1")

    # The DB path reached the TeamAgent method (no AttributeError) and used
    # it as the resume gate before calling resume_interrupt.
    handler._round.is_pending_interrupt_resume_valid.assert_called_once()
    handler._round.resume_interrupt.assert_awaited_once()
    delivered = handler._round.resume_interrupt.await_args.args[0]
    assert isinstance(delivered, InteractiveInput)
