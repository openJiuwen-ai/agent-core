# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``MessageHandler._harness_input_blocked`` (S_03 mechanism 18).

The gate decides, from the member's persisted status, whether the local
harness may be fed. F_118 adds the dormant exception: a dormant-bound member
with unread input is fed one last round instead of settling on top of it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole


def _gate(*, status: str, lifetime: str | None, unread: set[str], in_flight: bool = False) -> MessageHandler:
    handler = object.__new__(MessageHandler)
    handler._blueprint = SimpleNamespace(role=TeamRole.TEAMMATE)
    handler._infra = SimpleNamespace(
        team_backend=SimpleNamespace(
            get_member_status=AsyncMock(return_value=status),
            get_member_lifetime=AsyncMock(return_value=lifetime),
        ),
        message_manager=SimpleNamespace(members_with_unread=AsyncMock(return_value=unread)),
    )
    handler._round = SimpleNamespace(has_in_flight_round=lambda: in_flight)
    handler._lifecycle = SimpleNamespace(shutdown_self=AsyncMock())
    return handler


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("status", [MemberStatus.SHUTDOWN.value, MemberStatus.DORMANT.value])
async def test_gate_drops_delivery_to_a_member_without_runtime(status):
    handler = _gate(status=status, lifetime=None, unread=set())

    assert await handler._harness_input_blocked("dev-1") is True
    handler._lifecycle.shutdown_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_gate_settles_an_idle_shutdown_request_without_a_round():
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime=None, unread={"dev-1"})

    assert await handler._harness_input_blocked("dev-1") is True
    handler._lifecycle.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_gate_defers_a_dormant_settle_while_input_is_unread():
    """F_118: the message that arrived after the retire request is delivered, not stranded."""
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime="dormant", unread={"dev-1"})

    assert await handler._harness_input_blocked("dev-1") is False
    handler._lifecycle.shutdown_self.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_gate_settles_a_dormant_member_with_nothing_unread():
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime="dormant", unread=set())

    assert await handler._harness_input_blocked("dev-1") is True
    handler._lifecycle.shutdown_self.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_gate_lets_a_running_round_absorb_the_request():
    handler = _gate(status=MemberStatus.SHUTDOWN_REQUESTED.value, lifetime="dormant", unread=set(), in_flight=True)

    assert await handler._harness_input_blocked("dev-1") is False
    handler._lifecycle.shutdown_self.assert_not_awaited()


@pytest.fixture
def delivery_handler():
    handler = _gate(status=MemberStatus.BUSY.value, lifetime=None, unread=set(), in_flight=True)
    backend = handler._infra.team_backend
    backend.enable_message_delivery_modes = True
    backend.is_human_agent = AsyncMock(return_value=False)
    backend.resolve_leader_member_name = AsyncMock(return_value="leader")
    manager = handler._infra.message_manager
    manager.mark_messages_read = AsyncMock()
    handler._round.has_pending_interrupt = lambda: False
    handler._round.cancel_agent = AsyncMock()
    handler._round.deliver_input = AsyncMock()
    barrier = [None]
    handler._round.leader_stop = lambda: barrier[0]
    handler._round.set_leader_stop = lambda marker: barrier.__setitem__(0, marker)
    handler._active_task_versions = AsyncMock(return_value={"A": 1})
    handler._render_external_runtime_failed = lambda _: None
    handler._expand = AsyncMock()
    handler._format_message = lambda msg, **kwargs: msg.content
    handler._observe_shown_revision = AsyncMock()
    return handler


def delivery_mail(name, mode=None, sender="leader", timestamp=1, **meta):
    return SimpleNamespace(message_id=name, from_member_name=sender, timestamp=timestamp,
        broadcast=meta.get("receiver_routed", False), protocol="text", content=name,
        meta={"private_delivery_mode": mode, "target_tasks": {"A": 1}, **meta})


@pytest.mark.asyncio
async def test_follow_up_waits_for_task_end(delivery_handler):
    h = delivery_handler
    mail = delivery_mail("later", "follow_up")
    h._read_all_unread = AsyncMock(return_value=[mail])
    await h._process_unread_messages("dev-1")
    h._round.deliver_input.assert_not_awaited()
    h._infra.message_manager.mark_messages_read.assert_not_awaited()
    h._active_task_versions.return_value = {}
    await h._process_unread_messages("dev-1")
    h._round.deliver_input.assert_awaited_once_with("later", use_steer=False)
    h._infra.message_manager.mark_messages_read.assert_awaited_once_with([mail], "dev-1")


@pytest.mark.asyncio
async def test_abort_clears_mailbox_and_only_new_leader_input_resumes(delivery_handler):
    h = delivery_handler
    peer_abort = delivery_mail("unauthorized", "abort", sender="peer")
    h._read_all_unread = AsyncMock(return_value=[peer_abort])
    await h._process_unread_messages("dev-1")
    h._round.cancel_agent.assert_not_awaited()
    h._round.deliver_input.assert_not_awaited()
    pending = [delivery_mail("later", "follow_up"), delivery_mail("now", "steer"),
               delivery_mail("stop", "abort", timestamp=2)]
    h._read_all_unread.return_value = pending
    await h._process_unread_messages("dev-1")
    h._round.cancel_agent.assert_awaited_once()
    h._infra.message_manager.mark_messages_read.assert_awaited_with(pending, "dev-1")
    blocked = [delivery_mail("peer", sender="peer", timestamp=3),
               delivery_mail("broadcast", timestamp=3, receiver_routed=True),
               delivery_mail("scheduler", timestamp=3, template="task_assigned")]
    h._read_all_unread.return_value = blocked
    await h._process_unread_messages("dev-1")
    h._round.deliver_input.assert_not_awaited()
    h._read_all_unread.return_value = [delivery_mail("resume", "follow_up", timestamp=4)]
    h._round.deliver_input.side_effect = RuntimeError("admission rejected")
    with pytest.raises(RuntimeError, match="admission rejected"):
        await h._process_unread_messages("dev-1")
    assert h._round.leader_stop() is not None
    h._round.deliver_input.reset_mock(side_effect=True)
    await h._process_unread_messages("dev-1")
    assert h._round.leader_stop() is None
    h._round.deliver_input.assert_awaited_once_with("resume", use_steer=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["drop", "steer", "legacy"])
async def test_deferred_broadcast_does_not_block_newer_inputs(delivery_handler, monkeypatch, choice):
    h = delivery_handler
    task = SimpleNamespace(task_id="A", updated_at=1, status="in_progress", title="A", content="task")
    h._infra.task_manager = SimpleNamespace(get_tasks_by_assignee=AsyncMock(return_value=[task]))
    older, newer = [delivery_mail(name, receiver_routed=True) for name in ("older", "newer")]
    private = delivery_mail("private")
    async def save(message_id, member, mode, tasks):
        older.meta["delivery_feedback"] = {member: {"mode": mode, "target_tasks": tasks}}
    h._infra.message_manager.db = SimpleNamespace(message=SimpleNamespace(save_delivery_mode=save))
    choose = AsyncMock(side_effect=["follow_up", choice])
    monkeypatch.setattr("openjiuwen.agent_teams.agent.coordination.handlers.message.choose_delivery", choose)
    h._read_all_unread = AsyncMock(return_value=[older, newer, private])
    await h._process_unread_messages("dev-1")
    h._infra.message_manager.mark_messages_read.assert_awaited_once_with([newer, private], "dev-1")
    h._round.deliver_input.assert_any_await("private", use_steer=True)
    assert h._round.deliver_input.await_count == (1 if choice == "drop" else 2)
    h._read_all_unread.return_value = [older]
    h._active_task_versions.return_value = {}
    await h._process_unread_messages("dev-1")
    h._round.deliver_input.assert_awaited_with("older", use_steer=False)
    h._infra.message_manager.mark_messages_read.assert_awaited_with([older], "dev-1")
    assert choose.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("configured", [False, True])
async def test_jev_unavailable_falls_back(monkeypatch, group, configured):
    from openjiuwen.agent_teams.agent.coordination.handlers.message import choose_delivery
    for key in ("JEV_BASE_URL", "JEV_MODEL", "JEV_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    if configured:
        monkeypatch.setenv("JEV_BASE_URL", "http://unit-test.invalid")
    client = SimpleNamespace(system_one=AsyncMock(side_effect=TimeoutError), close=AsyncMock())
    monkeypatch.setattr("openjiuwen.agent_evolving.attribution.jev.JevClient", lambda **_: client)
    assert await choose_delivery({}, group=group) == ("steer" if group else "legacy")


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
async def test_jev_invalid_stop_choice_falls_back(monkeypatch, group):
    from openjiuwen.agent_teams.agent.coordination.handlers.message import choose_delivery
    monkeypatch.setenv("JEV_BASE_URL", "http://unit-test.invalid")
    client = SimpleNamespace(system_one=AsyncMock(return_value={"delivery": {"choice": "abort"}}), close=AsyncMock())
    monkeypatch.setattr("openjiuwen.agent_evolving.attribution.jev.JevClient", lambda **_: client)
    assert await choose_delivery({}, group=group) == ("steer" if group else "legacy")


@pytest.mark.asyncio
async def test_disabled_modes_deliver_legacy_input_without_jev_or_stop(delivery_handler):
    h = delivery_handler
    h._infra.team_backend.enable_message_delivery_modes = False
    message = delivery_mail("old", "follow_up")
    h._read_all_unread = AsyncMock(side_effect=[[message], []])
    h._stop_for_abort = AsyncMock()
    h._broadcast_mode = AsyncMock()
    await h._process_unread_messages("dev-1")
    h._round.deliver_input.assert_awaited_once()
    assert h._round.deliver_input.call_args.kwargs["use_steer"] is True
    h._stop_for_abort.assert_not_awaited()
    h._broadcast_mode.assert_not_awaited()
