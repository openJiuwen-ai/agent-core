# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent import agent_configurator as configurator_module
from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.debate import (
    DebateMessageRole,
    DebateRunState,
    make_debate_invocation_meta,
    normalize_debate_meta,
)
from openjiuwen.agent_teams.rails.debate_round_cap_rail import DebateRoundCapRail
from openjiuwen.agent_teams.rails.elements import build_team_debate_round_cap_rail
from openjiuwen.agent_teams.rails.team_context import inject_team_handles
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.rail.base import ModelCallInputs, ToolCallInputs
from openjiuwen.harness.tools.base_tool import ToolOutput


def _context(
    *,
    to: object = "peer",
    tool_name: str = "send_message",
    result: object = None,
    final_report: bool | None = None,
) -> SimpleNamespace:
    tool_args = {"to": to, "content": "message"}
    if final_report is not None:
        tool_args["final_report"] = final_report
    return SimpleNamespace(
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="call-1"),
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=result,
        ),
        extra={},
    )


def _rail(
    *,
    cap: int = 2,
    role: TeamRole = TeamRole.TEAMMATE,
    leader_name: str = "leader",
) -> DebateRoundCapRail:
    backend = MagicMock()
    backend.leader_member_name = leader_name
    backend.task_manager.list_tasks = AsyncMock(return_value=[])
    backend.resolve_leader_member_name = AsyncMock(return_value=leader_name)
    backend.list_member_roster = AsyncMock(return_value=[])
    backend.is_external_cli_agent = MagicMock(return_value=False)
    backend.message_manager.get_messages = AsyncMock(return_value=[])
    backend.message_manager.get_team_messages = AsyncMock(return_value=[])
    backend.message_manager.team_name = "team"
    backend.list_members = AsyncMock(
        return_value=[
            SimpleNamespace(member_name=name, role=TeamRole.TEAMMATE.value)
            for name in ("member-a", "member-b", "peer", "other")
        ]
    )
    backend.get_member = AsyncMock(
        return_value=SimpleNamespace(
            member_name="self",
            display_name="Data Analyst",
            role=TeamRole.TEAMMATE.value,
        ),
    )
    backend.debate_state = DebateRunState(language="en")
    return DebateRoundCapRail(
        max_debate_rounds=cap,
        team_backend=backend,
        member_name="self",
        role=role,
        language="en",
    )


def _model_context(*calls: ToolCall, extra: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        inputs=ModelCallInputs(response=AssistantMessage(content="", tool_calls=list(calls))),
        extra=extra or {},
    )


@pytest.mark.asyncio
async def test_counts_successful_peer_multicast_and_broadcast_calls_once_each() -> None:
    rail = _rail(cap=3)
    await rail._debate.activate_participant("round-1")

    for target in ("peer", ["peer", "other"], "*"):
        await rail.after_tool_call(
            _context(to=target, result=ToolOutput(success=True)),
        )

    assert rail._count == 3


@pytest.mark.asyncio
async def test_ignores_non_debate_targets_other_tools_and_failed_results() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")

    for target in ("leader", "user", "self", "", None):
        await rail.after_tool_call(
            _context(to=target, result=ToolOutput(success=True)),
        )
    await rail.after_tool_call(
        _context(tool_name="view_task", result=ToolOutput(success=True)),
    )
    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=False, error="failed")),
    )

    assert rail._count == 0


@pytest.mark.asyncio
async def test_debate_metadata_and_capped_state_are_round_scoped() -> None:
    state = DebateRunState(language="en")

    assert normalize_debate_meta(
        make_debate_invocation_meta("round-1", DebateMessageRole.PEER),
    )["message_role"] == "peer"
    assert normalize_debate_meta(
        make_debate_invocation_meta("round-1", DebateMessageRole.CAP_NOTICE),
    )["message_role"] == "cap_notice"

    await state.activate_participant("round-1")
    assert await state.mark_participant_capped("round-1") is True
    assert await state.is_participant_capped("round-1") is True

    await state.complete_participant("round-1")
    assert await state.is_participant_capped("round-1") is True

    await state.activate_participant("round-2")
    assert await state.is_participant_capped("round-1") is False
    assert await state.is_participant_capped("round-2") is False


@pytest.mark.asyncio
async def test_peer_send_is_tagged_and_last_send_caps_participant() -> None:
    rail = _rail(cap=2)
    await rail._debate.activate_participant("round-1")

    peer = _context(to="peer")
    await rail.before_tool_call(peer)
    assert normalize_debate_meta(peer.inputs.tool_args["_team_debate_meta"])["message_role"] == "peer"
    peer.inputs.tool_result = ToolOutput(success=True)
    peer.inputs.tool_msg = ToolMessage(content="sent", tool_call_id="call-1")
    await rail.after_tool_call(peer)

    last = _context(to="peer")
    await rail.before_tool_call(last)
    assert "Data Analyst has reached" in last.inputs.tool_args["content"]
    last.inputs.tool_result = ToolOutput(success=True)
    last.inputs.tool_msg = ToolMessage(content="sent", tool_call_id="call-1")
    await rail.after_tool_call(last)

    assert rail._count == 2
    assert await rail._debate.is_participant_capped("round-1") is True
    assert "final_report=true" in last.inputs.tool_msg.content


@pytest.mark.asyncio
async def test_failed_last_send_does_not_cap_participant() -> None:
    rail = _rail(cap=2)
    await rail._debate.activate_participant("round-1")
    await rail.after_tool_call(_context(to="peer", result=ToolOutput(success=True)))

    failed = _context(to="peer")
    await rail.before_tool_call(failed)
    failed.inputs.tool_result = ToolOutput(success=False, error="failed")
    failed.inputs.tool_msg = ToolMessage(content="failed", tool_call_id="call-1")
    await rail.after_tool_call(failed)

    assert rail._count == 1
    assert await rail._debate.is_participant_capped("round-1") is False


@pytest.mark.asyncio
async def test_over_limit_peer_send_becomes_uncounted_cap_notice() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")
    await rail.after_tool_call(_context(to="peer", result=ToolOutput(success=True)))

    peer = _context(to="peer")
    await rail.before_tool_call(peer)

    assert peer.extra.get("_skip_tool") is None
    assert "Data Analyst has reached" in peer.inputs.tool_args["content"]
    assert normalize_debate_meta(peer.inputs.tool_args["_team_debate_meta"])["message_role"] == "cap_notice"

    peer.inputs.tool_result = ToolOutput(success=True)
    peer.inputs.tool_msg = ToolMessage(content="sent", tool_call_id="call-1")
    await rail.after_tool_call(peer)

    assert rail._count == 1


@pytest.mark.asyncio
async def test_only_explicit_final_report_to_leader_is_tagged() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")
    ordinary_leader = _context(to="leader", result=ToolOutput(success=True))
    await rail.before_tool_call(ordinary_leader)
    await rail.after_tool_call(ordinary_leader)
    final_report = _context(to="leader", final_report=True)
    await rail.before_tool_call(final_report)

    assert ordinary_leader.extra.get("_skip_tool") is None
    assert "_team_debate_meta" not in ordinary_leader.inputs.tool_args
    assert rail._debate.participant_round_id == "round-1"
    assert normalize_debate_meta(final_report.inputs.tool_args["_team_debate_meta"]) == {
        "kind": "team_debate",
        "round_id": "round-1",
        "message_role": "final_report",
    }
    assert rail._count == 0


@pytest.mark.asyncio
async def test_successful_final_report_closes_teammate_round_but_failure_can_retry() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")
    failed = _context(
        to="leader",
        final_report=True,
        result=ToolOutput(success=False, error="failed"),
    )
    await rail.before_tool_call(failed)
    await rail.after_tool_call(failed)
    assert rail._debate.participant_round_id == "round-1"

    succeeded = _context(
        to="leader",
        final_report=True,
        result=ToolOutput(success=True),
    )
    await rail.before_tool_call(succeeded)
    await rail.after_tool_call(succeeded)

    assert rail._debate.participant_round_id is None


@pytest.mark.asyncio
async def test_final_report_requires_a_string_leader_target() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")
    leader_list = _context(
        to=["leader"],
        final_report=True,
        result=ToolOutput(success=True),
    )

    await rail.before_tool_call(leader_list)
    await rail.after_tool_call(leader_list)

    assert "_team_debate_meta" not in leader_list.inputs.tool_args
    assert rail._debate.participant_round_id == "round-1"


@pytest.mark.asyncio
async def test_literal_leader_is_a_peer_when_real_leader_has_another_name() -> None:
    rail = _rail(cap=1, leader_name="office")
    await rail._debate.activate_participant("round-1")
    peer_named_leader = _context(
        to="leader",
        final_report=True,
        result=ToolOutput(success=True),
    )

    await rail.before_tool_call(peer_named_leader)
    await rail.after_tool_call(peer_named_leader)

    assert normalize_debate_meta(
        peer_named_leader.inputs.tool_args["_team_debate_meta"],
    )["message_role"] == "peer"
    assert rail._debate.participant_round_id == "round-1"
    assert rail._count == 1


@pytest.mark.asyncio
async def test_final_report_uses_the_real_leader_member_name() -> None:
    rail = _rail(cap=1, leader_name="office")
    await rail._debate.activate_participant("round-1")
    final_report = _context(
        to="office",
        final_report=True,
        result=ToolOutput(success=True),
    )

    await rail.before_tool_call(final_report)
    assert normalize_debate_meta(final_report.inputs.tool_args["_team_debate_meta"]) == {
        "kind": "team_debate",
        "round_id": "round-1",
        "message_role": "final_report",
    }
    await rail.after_tool_call(final_report)

    assert rail._debate.participant_round_id is None


@pytest.mark.asyncio
async def test_open_board_task_disables_counting_and_rejection() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")
    rail._team.task_manager.list_tasks.return_value = [
        SimpleNamespace(status="in_progress"),
    ]

    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )
    rail._count = 1
    peer = _context(to="peer")
    await rail.before_tool_call(peer)

    assert rail._count == 1
    assert peer.extra.get("_skip_tool") is None


@pytest.mark.asyncio
async def test_task_query_failure_fails_open_without_counting_or_rejecting() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")
    rail._team.task_manager.list_tasks.side_effect = RuntimeError("db unavailable")

    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )
    rail._count = 1
    peer = _context(to="peer")
    await rail.before_tool_call(peer)

    assert rail._count == 1
    assert peer.extra.get("_skip_tool") is None


@pytest.mark.asyncio
async def test_task_query_failure_stays_fail_open_when_query_recovers_after_send() -> None:
    rail = _rail(cap=1)
    await rail._debate.activate_participant("round-1")
    rail._team.task_manager.list_tasks.side_effect = [RuntimeError("db unavailable"), []]
    peer = _context(to="peer", result=ToolOutput(success=True))

    await rail.before_tool_call(peer)
    await rail.after_tool_call(peer)

    assert peer.extra.get("_skip_tool") is None
    assert rail._count == 0


@pytest.mark.asyncio
async def test_leader_wakes_once_after_all_successful_invites_report_or_fail() -> None:
    rail = _rail(role=TeamRole.LEADER)
    wake = AsyncMock(return_value=True)
    rail._debate.bind_leader_wakeup(wake)
    calls = (
        ToolCall(id="call-a", type="function", name="send_message", arguments=json.dumps({"to": "member-a"})),
        ToolCall(id="call-b", type="function", name="send_message", arguments=json.dumps({"to": "member-b"})),
    )

    await rail.after_model_call(_model_context(*calls))
    invite_a = _context(to="member-a", result=ToolOutput(success=True))
    invite_a.inputs.tool_call.id = "call-a"
    invite_b = _context(to="member-b", result=ToolOutput(success=True))
    invite_b.inputs.tool_call.id = "call-b"
    await rail.before_tool_call(invite_a)
    await rail.before_tool_call(invite_b)
    round_id = normalize_debate_meta(invite_a.inputs.tool_args["_team_debate_meta"])["round_id"]
    await rail.after_tool_call(invite_a)
    await rail.after_tool_call(invite_b)

    await rail._debate.capture_report(round_id, "member-a", "report A")
    wake.assert_not_awaited()
    await rail._debate.mark_failed("member-b")
    wake.assert_awaited_once()
    assert "report A" in wake.await_args.args[0]
    assert "member-b" in wake.await_args.args[0]

    await rail._debate.capture_report(round_id, "member-a", "duplicate")
    wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_grace_refreshes_and_finalizes_pending_members_after_expiry(
) -> None:
    now = 100.0
    state = DebateRunState(language="en")
    state._clock = lambda: now
    wake = AsyncMock(return_value=True)
    state.bind_leader_wakeup(wake)
    round_id = await state.begin_round(
        {"call-a": {"member-a", "member-b", "member-c"}},
    )
    await state.settle_invitation("call-a", succeeded=True)

    await state.capture_report(round_id, "member-a", "report A")
    now = 200.0
    await state.capture_report(round_id, "member-a", "duplicate report A")
    assert state._terminal_deadline == 400.0

    now = 399.0
    await state.mark_failed("member-b")
    now = 698.0
    await state.retry_finalization()
    wake.assert_not_awaited()

    now = 699.0
    await state.retry_finalization()

    wake.assert_awaited_once()
    assert state.unreported_participants == {"member-c"}
    assert "member-c" in wake.await_args.args[0]


@pytest.mark.asyncio
async def test_terminal_grace_timer_finalizes_without_another_mailbox_event() -> None:
    state = DebateRunState(language="en")
    state._terminal_grace_seconds = 0.01
    wake = AsyncMock(return_value=True)
    state.bind_leader_wakeup(wake)
    round_id = await state.begin_round({"call-a": {"member-a", "member-b"}})
    await state.settle_invitation("call-a", succeeded=True)

    await state.capture_report(round_id, "member-a", "report A")
    await asyncio.sleep(0.03)

    wake.assert_awaited_once()
    assert state.unreported_participants == {"member-b"}


@pytest.mark.asyncio
async def test_terminal_grace_suspends_across_run_cycle_teardown() -> None:
    state = DebateRunState(language="en")
    state._terminal_grace_seconds = 0.01
    wake = AsyncMock(return_value=True)
    state.bind_leader_wakeup(wake)
    round_id = await state.begin_round({"call-a": {"member-a", "member-b"}})
    await state.settle_invitation("call-a", succeeded=True)
    await state.capture_report(round_id, "member-a", "report A")

    state.suspend_terminal_grace()
    await asyncio.sleep(0.03)

    wake.assert_not_awaited()
    assert state._terminal_timer_task is None

    await state.resume_terminal_grace()

    wake.assert_awaited_once()
    assert state.unreported_participants == {"member-b"}


@pytest.mark.asyncio
async def test_terminal_event_after_deadline_cannot_refresh_expired_grace() -> None:
    now = 100.0
    state = DebateRunState(language="en")
    state._clock = lambda: now
    wake = AsyncMock(return_value=True)
    state.bind_leader_wakeup(wake)
    round_id = await state.begin_round({"call-a": {"member-a", "member-b"}})
    await state.settle_invitation("call-a", succeeded=True)
    await state.capture_report(round_id, "member-a", "report A")

    now = 401.0
    accepted = await state.capture_report(round_id, "member-b", "late report B")

    assert accepted is False
    wake.assert_awaited_once()
    assert state.reports == {"member-a": "report A"}
    assert state.unreported_participants == {"member-b"}


@pytest.mark.asyncio
async def test_teammate_rejects_peer_that_already_sent_a_final_report() -> None:
    rail = _rail()
    await rail._debate.activate_participant("round-1")
    rail._team.message_manager.get_messages.return_value = [
        SimpleNamespace(
            from_member_name="peer",
            coordination_meta=normalize_debate_meta(
                make_debate_invocation_meta(
                    "round-1",
                    DebateMessageRole.FINAL_REPORT,
                ),
            ),
        ),
    ]
    peer = _context(to="peer")

    await rail.before_tool_call(peer)

    assert peer.extra["_skip_tool"] is True
    assert "already completed" in peer.inputs.tool_result["error"]
    assert rail._count == 0


@pytest.mark.asyncio
async def test_teammate_rejects_broadcast_when_all_invited_peers_are_terminal() -> None:
    rail = _rail()
    await rail._debate.activate_participant("round-1")
    rail._team.message_manager.get_team_messages.return_value = [
        SimpleNamespace(
            broadcast=False,
            to_member_name=member_name,
            coordination_meta=normalize_debate_meta(
                make_debate_invocation_meta(
                    "round-1",
                    DebateMessageRole.INVITE,
                ),
            ),
        )
        for member_name in ("self", "peer")
    ]
    rail._team.message_manager.get_messages.return_value = [
        SimpleNamespace(
            from_member_name="peer",
            coordination_meta=normalize_debate_meta(
                make_debate_invocation_meta(
                    "round-1",
                    DebateMessageRole.FINAL_REPORT,
                ),
            ),
        )
    ]
    broadcast = _context(to="*")

    await rail.before_tool_call(broadcast)

    assert broadcast.extra["_skip_tool"] is True
    assert "No active peers remain" in broadcast.inputs.tool_result["error"]
    assert rail._count == 0


@pytest.mark.asyncio
async def test_partial_multicast_tracks_only_members_that_received_the_invite() -> None:
    rail = _rail(role=TeamRole.LEADER)
    wake = AsyncMock(return_value=True)
    rail._debate.bind_leader_wakeup(wake)
    call = ToolCall(
        id="call-a",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": ["member-a", "missing"]}),
    )

    await rail.after_model_call(_model_context(call))
    invite = _context(
        to=["member-a", "missing"],
        result=ToolOutput(
            success=False,
            error="partial failure",
            data={
                "type": "multicast",
                "delivered": ["member-a"],
                "failed": [{"to": "missing", "reason": "not found"}],
            },
        ),
    )
    invite.inputs.tool_call.id = "call-a"

    await rail.after_tool_call(invite)

    assert rail._debate.expected_participants == {"member-a"}
    wake.assert_not_awaited()
    await rail._debate.capture_report(rail._debate.round_id, "member-a", "report A")
    wake.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("temporarily unavailable"), asyncio.CancelledError()],
)
async def test_failed_or_cancelled_leader_wakeup_can_retry_without_losing_reports(
    failure: BaseException,
) -> None:
    state = DebateRunState(language="en")
    wake = AsyncMock(side_effect=[failure, True])
    state.bind_leader_wakeup(wake)
    round_id = await state.begin_round({"call-a": {"member-a"}})
    await state.settle_invitation("call-a", succeeded=True)

    with pytest.raises(type(failure)):
        await state.capture_report(round_id, "member-a", "report A")

    assert state.finalized is False
    assert state.finalizing is False
    assert state.reports == {"member-a": "report A"}

    await state.capture_report(round_id, "member-a", "report A")

    assert state.finalized is True
    assert wake.await_count == 2


@pytest.mark.asyncio
async def test_new_rail_resets_local_unfinished_state_but_preserves_finalized_leader_round() -> None:
    backend = MagicMock()
    backend.leader_member_name = "leader"
    backend.task_manager.list_tasks = AsyncMock(return_value=[])
    backend.resolve_leader_member_name = AsyncMock(return_value="leader")
    backend.list_members = AsyncMock(return_value=[])
    backend.debate_state = DebateRunState(language="en")
    await backend.debate_state.activate_participant("old-participant-round")

    DebateRoundCapRail(
        max_debate_rounds=2,
        team_backend=backend,
        member_name="member-a",
        role=TeamRole.TEAMMATE,
        language="en",
    )
    assert backend.debate_state.participant_round_id is None

    await backend.debate_state.begin_round({"stale-call": {"member-a"}})
    DebateRoundCapRail(
        max_debate_rounds=2,
        team_backend=backend,
        member_name="leader",
        role=TeamRole.LEADER,
        language="en",
    )
    assert backend.debate_state.round_id is None

    backend.debate_state.round_id = "completed-round"
    backend.debate_state.invitation_calls = {
        "old-call": frozenset({"member-a"}),
    }
    backend.debate_state.finalized = True
    leader_rail = DebateRoundCapRail(
        max_debate_rounds=2,
        team_backend=backend,
        member_name="leader",
        role=TeamRole.LEADER,
        language="en",
    )
    assert backend.debate_state.round_id == "completed-round"
    assert backend.debate_state.finalized is True

    new_invite = ToolCall(
        id="new-call",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": "member-a"}),
    )
    await leader_rail.after_model_call(_model_context(new_invite))

    assert backend.debate_state.invitation_calls == {
        "old-call": frozenset({"member-a"}),
    }


@pytest.mark.asyncio
async def test_leader_tracks_only_teammates_that_can_tag_final_reports() -> None:
    rail = _rail(role=TeamRole.LEADER)
    wake = AsyncMock(return_value=True)
    rail._debate.bind_leader_wakeup(wake)
    rail._team.list_members.return_value = [
        SimpleNamespace(member_name="member-a", role=TeamRole.TEAMMATE.value),
        SimpleNamespace(
            member_name="failed-a",
            role=TeamRole.TEAMMATE.value,
            status="error",
            execution_status="failed",
        ),
        SimpleNamespace(member_name="external-a", role=TeamRole.TEAMMATE.value),
        SimpleNamespace(member_name="human-a", role=TeamRole.HUMAN_AGENT.value),
        SimpleNamespace(member_name="bridge-a", role=TeamRole.BRIDGE_AGENT.value),
    ]
    rail._team.is_external_cli_agent.side_effect = lambda name: name == "external-a"
    call = ToolCall(
        id="call-a",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": "*"}),
    )

    await rail.after_model_call(_model_context(call))

    assert rail._debate.invitation_calls == {
        "call-a": frozenset({"failed-a", "member-a"}),
    }
    assert rail._debate.failed_participants == {"failed-a"}

    invite = _context(to="*", result=ToolOutput(success=True))
    invite.inputs.tool_call.id = "call-a"
    await rail.after_tool_call(invite)
    await rail._debate.capture_report(rail._debate.round_id, "member-a", "report A")

    wake.assert_awaited_once()
    assert "failed-a" in wake.await_args.args[0]


@pytest.mark.asyncio
async def test_report_and_failure_are_mutually_exclusive() -> None:
    state = DebateRunState(language="en")
    wake = AsyncMock(return_value=True)
    state.bind_leader_wakeup(wake)
    round_id = await state.begin_round({"call-a": {"member-a"}})

    await state.capture_report(round_id, "member-a", "report A")
    await state.mark_failed("member-a")
    await state.settle_invitation("call-a", succeeded=True)

    assert state.reports == {"member-a": "report A"}
    assert state.failed_participants == set()
    prompt = wake.await_args.args[0]
    assert "report A" in prompt
    assert "failed to report" not in prompt


@pytest.mark.asyncio
async def test_leader_does_not_replace_an_active_debate_round() -> None:
    rail = _rail(role=TeamRole.LEADER)
    first = ToolCall(
        id="call-a",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": "member-a"}),
    )
    second = ToolCall(
        id="call-b",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": "member-b"}),
    )

    await rail.after_model_call(_model_context(first))
    round_id = rail._debate.round_id
    await rail.after_model_call(_model_context(second))

    assert rail._debate.round_id == round_id
    assert set(rail._debate.invitation_calls) == {"call-a"}


@pytest.mark.asyncio
async def test_leader_does_not_reopen_a_finalized_round_from_internal_summary() -> None:
    rail = _rail(role=TeamRole.LEADER)
    wake = AsyncMock(return_value=True)
    rail._debate.bind_leader_wakeup(wake)
    first = ToolCall(
        id="call-a",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": "member-a"}),
    )
    await rail.after_model_call(_model_context(first))
    invite = _context(to="member-a", result=ToolOutput(success=True))
    invite.inputs.tool_call.id = "call-a"
    await rail.after_tool_call(invite)
    round_id = rail._debate.round_id
    await rail._debate.capture_report(round_id, "member-a", "report A")
    assert rail._debate.finalized is True

    second = ToolCall(
        id="call-b",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": "member-b"}),
    )
    await rail.after_model_call(_model_context(second))

    assert rail._debate.round_id == round_id
    assert set(rail._debate.invitation_calls) == {"call-a"}


@pytest.mark.asyncio
async def test_leader_settles_registered_invitation_skipped_by_another_rail() -> None:
    rail = _rail(role=TeamRole.LEADER)
    wake = AsyncMock(return_value=True)
    rail._debate.bind_leader_wakeup(wake)
    call = ToolCall(
        id="call-a",
        type="function",
        name="send_message",
        arguments=json.dumps({"to": "member-a"}),
    )
    await rail.after_model_call(_model_context(call))
    skipped = _context(to="member-a")
    skipped.inputs.tool_call.id = "call-a"
    skipped.extra["_skip_tool"] = True

    await rail.after_tool_call(skipped)

    assert rail._debate.pending_invitation_calls == set()
    assert rail._debate.expected_participants == set()
    wake.assert_awaited_once()


@pytest.mark.parametrize(
    ("role", "cap", "expected"),
    [
        ("teammate", 2, True),
        ("leader", 2, True),
        ("teammate", 0, False),
    ],
)
def test_provider_builds_only_for_enabled_teammates(role: str, cap: int, expected: bool) -> None:
    backend = MagicMock(team_name="team", db=MagicMock())
    context = BuildContext(member_name="member", role=role, language="en")
    inject_team_handles(
        context.extras,
        team_backend=backend,
        messager=MagicMock(),
    )

    result = build_team_debate_round_cap_rail(
        {"max_debate_rounds": cap, "team_name": "team"},
        context,
    )

    assert isinstance(result, DebateRoundCapRail) is expected


def test_configurator_declares_cap_only_for_enabled_teammates(monkeypatch) -> None:
    captured: list[dict] = []

    def fake_build(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(workspace=None, sys_operation=None, model=None)

    monkeypatch.setattr(configurator_module.TeamHarness, "build", fake_build)
    configurator = AgentConfigurator(card=AgentCard(id="team", name="team", description="team"))
    spec = TeamAgentSpec(
        team_name="team",
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        max_debate_rounds=2,
    )
    team_spec = TeamSpec(team_name="team", display_name="team", leader_member_name="leader")

    for role, member_name in (
        (TeamRole.TEAMMATE, "member"),
        (TeamRole.LEADER, "leader"),
    ):
        configurator.setup_agent(
            spec,
            TeamRuntimeContext(role=role, member_name=member_name, team_spec=team_spec),
        )

    teammate_rails = captured[0]["agent_spec"].rails
    leader_rails = captured[1]["agent_spec"].rails
    teammate_cap = [rail for rail in teammate_rails if rail.type == "core.team.debate_round_cap"]
    leader_cap = [rail for rail in leader_rails if rail.type == "core.team.debate_round_cap"]
    assert len(teammate_cap) == 1
    assert teammate_cap[0].params == {"max_debate_rounds": 2, "team_name": "team"}
    assert len(leader_cap) == 1


def test_configurator_does_not_mount_debate_rail_for_scheduled_mode(monkeypatch) -> None:
    captured: list[dict] = []

    def fake_build(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(workspace=None, sys_operation=None, model=None)

    monkeypatch.setattr(configurator_module.TeamHarness, "build", fake_build)
    configurator = AgentConfigurator(card=AgentCard(id="team", name="team", description="team"))
    spec = TeamAgentSpec(
        team_name="team",
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        max_debate_rounds=2,
        dispatch_mode="scheduled",
    )
    team_spec = TeamSpec(team_name="team", display_name="team", leader_member_name="leader")

    configurator.setup_agent(
        spec,
        TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec),
    )

    rails = captured[0]["agent_spec"].rails
    assert all(rail.type != "core.team.debate_round_cap" for rail in rails)
