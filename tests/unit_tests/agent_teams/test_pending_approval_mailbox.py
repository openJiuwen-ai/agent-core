# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pending approvals must survive reminders, queued mail and resume windows."""

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.agent.coordination.handlers.stale_task import ScheduledStaleTaskHandler, StaleTaskHandler
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.session import InteractiveInput


class Mailbox:
    def __init__(self, messages):
        self.messages = list(messages)
        self.read = []

    async def get_messages(self, **_kwargs):
        return [m for m in self.messages if not m.broadcast]

    async def get_broadcast_messages(self, **_kwargs):
        return [m for m in self.messages if m.broadcast]

    async def mark_message_read(self, message_id, _member):
        self.read.append(message_id)
        self.messages = [m for m in self.messages if m.message_id != message_id]
        return True

    async def mark_messages_read(self, messages, member):
        for msg in messages:
            await self.mark_message_read(msg.message_id, member)


def mail(message_id, *, approval=False, sender="leader", broadcast=False, **payload):
    data = {"type": "tool_approval_result", "tool_call_id": "call-a", "approved": True, **payload}
    return NS(
        message_id=message_id,
        from_member_name=sender,
        broadcast=broadcast,
        timestamp=int(message_id),
        protocol="json" if approval else "text",
        content=json.dumps(data) if approval else message_id,
    )


def mailbox_handler(messages, host=None):
    host = host or NS(
        has_pending_interrupt=lambda: True,
        has_in_flight_round=lambda: False,
        resume_interrupt=AsyncMock(),
        deliver_input=AsyncMock(),
    )
    mailbox = Mailbox(messages)
    blueprint = NS(role=TeamRole.TEAMMATE, member_name="worker", team_spec=NS(leader_member_name="leader"))
    handler = MessageHandler(host, blueprint, NS(message_manager=mailbox, team_backend=None), None)
    handler._expand = AsyncMock(return_value=NS())
    handler._format_message = lambda msg, **_kwargs: msg.message_id
    return handler, mailbox, host


@pytest.mark.asyncio
async def test_scan_past_ordinary_mail_and_bind_approval_to_tool_id():
    handler, mailbox, host = mailbox_handler([mail("3"), mail("2", approval=True), mail("1")])
    await handler._process_unread_messages("worker")
    reply = host.resume_interrupt.await_args.args[0]
    assert isinstance(reply, InteractiveInput)
    assert reply.user_inputs == {"call-a": {"approved": True, "feedback": "", "auto_confirm": False}}
    assert mailbox.read == ["2"]
    host.deliver_input.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_in_flight_resume_keeps_approval_unread_even_when_state_is_temporarily_empty(pending):
    host = NS(
        has_pending_interrupt=lambda: pending,
        has_in_flight_round=lambda: True,
        resume_interrupt=AsyncMock(),
        deliver_input=AsyncMock(),
    )
    handler, mailbox, _ = mailbox_handler([mail("1", approval=True)], host)
    await handler._process_unread_messages("worker")
    assert mailbox.read == []
    host.resume_interrupt.assert_not_awaited()
    host.deliver_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_one_approval_is_submitted_per_drain():
    handler, mailbox, host = mailbox_handler([mail("2", approval=True), mail("1", approval=True)])
    await handler._process_unread_messages("worker")
    host.resume_interrupt.assert_awaited_once()
    assert mailbox.read == ["1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        {"from_member_name": "other"},
        {"broadcast": True},
        {"tool_call_id": ""},
        {"approved": "true"},
        {"approved": 1},
        {"approved": None},
        {"auto_confirm": "true"},
        {"auto_confirm": 1},
        {"feedback": False},
        {"missing_approved": True},
        {"bad_json": True},
    ],
)
async def test_untrusted_or_malformed_mail_never_resumes(change, pending):
    msg = mail("1", approval=True)
    data = json.loads(msg.content)
    for key, value in change.items():
        if key in {"from_member_name", "broadcast"}:
            setattr(msg, key, value)
        else:
            data[key] = value
    if change.get("missing_approved"):
        data.pop("approved")
    msg.content = "{" if change.get("bad_json") else json.dumps(data)
    handler, mailbox, host = mailbox_handler([msg])
    host.has_pending_interrupt = lambda: pending
    await handler._process_unread_messages("worker")
    host.resume_interrupt.assert_not_awaited()
    if change.get("bad_json") or change.get("broadcast"):
        assert mailbox.read == ([] if pending else ["1"])
    else:
        host.deliver_input.assert_not_awaited()
        assert mailbox.read == ["1"]


@pytest.mark.asyncio
async def test_legacy_null_options_resume_with_safe_defaults():
    handler, mailbox, host = mailbox_handler([mail("1", approval=True, feedback=None, auto_confirm=None)])
    await handler._process_unread_messages("worker")
    assert host.resume_interrupt.await_args.args[0].user_inputs == {
        "call-a": {"approved": True, "feedback": "", "auto_confirm": False}
    }
    assert mailbox.read == ["1"]


@pytest.mark.asyncio
async def test_unresolved_leader_keeps_control_mail_until_it_can_be_authenticated():
    handler, mailbox, host = mailbox_handler([mail("1", approval=True)])
    handler._blueprint.team_spec = None
    resolver = AsyncMock(return_value="")
    handler._infra.team_backend = NS(get_member_status=AsyncMock(return_value="ready"),
                                    is_human_agent=AsyncMock(return_value=False),
                                    resolve_leader_member_name=resolver)
    await handler._process_unread_messages("worker")
    assert mailbox.read == []
    host.resume_interrupt.assert_not_awaited()
    host.deliver_input.assert_not_awaited()
    resolver.return_value = "leader"
    await handler._process_unread_messages("worker")
    host.resume_interrupt.assert_awaited_once()
    assert mailbox.read == ["1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("admission failed"), asyncio.CancelledError()])
async def test_approval_is_not_acknowledged_before_resume_admission(error):
    handler, mailbox, host = mailbox_handler([mail("1", approval=True)])
    host.resume_interrupt.side_effect = error
    with pytest.raises(type(error)):
        await handler._process_unread_messages("worker")
    assert mailbox.read == []


@pytest.mark.asyncio
async def test_stale_control_mail_is_consumed_without_becoming_a_query():
    host = NS(
        has_pending_interrupt=lambda: False,
        has_in_flight_round=lambda: False,
        resume_interrupt=AsyncMock(),
        deliver_input=AsyncMock(),
    )
    handler, mailbox, _ = mailbox_handler([mail("1", approval=True)], host)
    await handler._process_unread_messages("worker")
    assert mailbox.read == ["1"]
    host.deliver_input.assert_not_awaited()
    host.resume_interrupt.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_mail_is_bounded_and_recovers_in_original_order():
    pending = NS(value=True)
    host = NS(
        has_pending_interrupt=lambda: pending.value,
        has_in_flight_round=lambda: False,
        resume_interrupt=AsyncMock(),
        deliver_input=AsyncMock(),
    )
    handler, mailbox, _ = mailbox_handler([mail("3"), mail("2"), mail("1")], host)
    await asyncio.wait_for(handler._process_unread_messages("worker"), 1)
    assert mailbox.read == []
    host.deliver_input.assert_not_awaited()
    pending.value = False
    await handler._process_unread_messages("worker")
    assert [c.args[0] for c in host.deliver_input.await_args_list] == ["1", "2", "3"]


def stale_handler(cls, *, pending=False, flip_during_query=False):
    state = NS(pending=pending)
    host = NS(
        has_pending_interrupt=lambda: state.pending,
        has_in_flight_round=lambda: False,
        idle_seconds=lambda: 1000,
        is_agent_running=lambda: False,
        deliver_input=AsyncMock(),
    )
    task = NS(task_id="task", assignee="worker", updated_at=0, title="PPT", status="in_progress")

    async def tasks(**_kwargs):
        if flip_during_query:
            state.pending = True
        return [task]

    manager = NS(list_tasks=AsyncMock(side_effect=tasks))
    blueprint = NS(
        member_name="worker",
        role=TeamRole.TEAMMATE,
        spec=NS(stale_claim_idle_timeout=600, stale_pending_idle_timeout=600),
    )
    handler = cls(host, blueprint, NS(task_manager=manager, team_backend=None), None)
    return handler, host, manager


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [StaleTaskHandler, ScheduledStaleTaskHandler])
@pytest.mark.parametrize("flip_during_query", [False, True])
async def test_pending_approval_is_not_nudged_or_counted(cls, flip_during_query):
    handler, host, manager = stale_handler(cls, pending=not flip_during_query, flip_during_query=flip_during_query)
    await handler.on_poll_task(None)
    await handler.on_poll_task(None)
    host.deliver_input.assert_not_awaited()
    assert handler._last_stale_nudge == {}
    assert handler._stale_claim_streak == {}
    if not flip_during_query:
        manager.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_leader_is_not_prompted_after_roster_query():
    handler, host, manager = stale_handler(StaleTaskHandler)
    handler._blueprint.role = TeamRole.LEADER
    state = NS(pending=False)
    host.has_pending_interrupt = lambda: state.pending
    task = NS(task_id="pending", assignee=None, updated_at=0, title="PPT")
    manager.list_tasks.return_value = [task]
    manager.list_tasks.side_effect = None

    async def roster():
        state.pending = True
        return [NS(status="ready")]

    handler._infra.team_backend = NS(list_member_roster=roster)
    await handler._check_stale_pending_tasks()
    host.deliver_input.assert_not_awaited()
    assert handler._last_pending_nudge == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("message_id", [None, "saved-approval"])
async def test_producer_persists_before_publishing_and_reports_write_failure(message_id):
    order = []

    async def persist(**kwargs):
        order.append("persist")
        assert kwargs["protocol"] == "json"
        assert json.loads(kwargs["content"])["tool_call_id"] == "call-a"
        return message_id

    async def publish(**kwargs):
        assert order == ["persist"]
        order.append("publish")

    backend = NS(
        team_name="team",
        db=NS(member=NS(get_member=AsyncMock(return_value=object()))),
        message_manager=NS(send_message=persist),
        messager=NS(publish=publish),
    )
    result = await TeamBackend.approve_tool(backend, "worker", "call-a", True)
    assert result is bool(message_id)
    assert order == (["persist", "publish"] if message_id else ["persist"])
