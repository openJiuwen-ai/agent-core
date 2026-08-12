# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent import agent_configurator as configurator_module
from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.rails.debate_round_cap_rail import DebateRoundCapRail
from openjiuwen.agent_teams.rails.elements import build_team_debate_round_cap_rail
from openjiuwen.agent_teams.rails.team_context import inject_team_handles
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.harness.tools.base_tool import ToolOutput


def _context(
    *,
    to: object = "peer",
    tool_name: str = "send_message",
    result: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="call-1"),
            tool_name=tool_name,
            tool_args={"to": to, "content": "message"},
            tool_result=result,
        ),
        extra={},
    )


def _rail(*, cap: int = 2) -> DebateRoundCapRail:
    backend = MagicMock()
    backend.leader_member_name = "leader"
    backend.task_manager.list_tasks = AsyncMock(return_value=[])
    backend.resolve_leader_member_name = AsyncMock(return_value="leader")
    messages = MagicMock()
    messages.send_message = AsyncMock(return_value="message-1")
    return DebateRoundCapRail(
        max_debate_rounds=cap,
        team_backend=backend,
        message_manager=messages,
        member_name="self",
        language="en",
    )


@pytest.mark.asyncio
async def test_counts_successful_peer_multicast_and_broadcast_calls_once_each() -> None:
    rail = _rail(cap=3)

    for target in ("peer", ["peer", "other"], "*"):
        await rail.after_tool_call(
            _context(to=target, result=ToolOutput(success=True)),
        )

    assert rail._count == 3
    rail._messages.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_ignores_non_debate_targets_other_tools_and_failed_results() -> None:
    rail = _rail(cap=1)

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
    rail._messages.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_cap_notifies_leader_once_rejects_peers_but_allows_final_report() -> None:
    rail = _rail(cap=1)
    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )

    peer = _context(to="peer")
    await rail.before_tool_call(peer)
    await rail.after_tool_call(peer)
    broadcast = _context(to="*")
    await rail.before_tool_call(broadcast)
    leader = _context(to="leader")
    await rail.before_tool_call(leader)

    assert peer.extra["_skip_tool"] is True
    assert broadcast.extra["_skip_tool"] is True
    assert peer.inputs.tool_result["error"]
    assert leader.extra.get("_skip_tool") is None
    assert rail._count == 1
    rail._messages.send_message.assert_awaited_once()
    assert rail._messages.send_message.await_args.kwargs["to_member_name"] == "leader"


@pytest.mark.asyncio
async def test_open_board_task_disables_counting_and_rejection() -> None:
    rail = _rail(cap=1)
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
    rail._messages.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_query_failure_fails_open_without_counting_or_rejecting() -> None:
    rail = _rail(cap=1)
    rail._team.task_manager.list_tasks.side_effect = RuntimeError("db unavailable")

    await rail.after_tool_call(
        _context(to="peer", result=ToolOutput(success=True)),
    )
    rail._count = 1
    peer = _context(to="peer")
    await rail.before_tool_call(peer)

    assert rail._count == 1
    assert peer.extra.get("_skip_tool") is None
    rail._messages.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_query_failure_stays_fail_open_when_query_recovers_after_send() -> None:
    rail = _rail(cap=1)
    rail._team.task_manager.list_tasks.side_effect = [RuntimeError("db unavailable"), []]
    peer = _context(to="peer", result=ToolOutput(success=True))

    await rail.before_tool_call(peer)
    await rail.after_tool_call(peer)

    assert peer.extra.get("_skip_tool") is None
    assert rail._count == 0
    rail._messages.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_leader_notification_can_retry_without_duplicates() -> None:
    rail = _rail(cap=1)
    rail._messages.send_message.side_effect = [RuntimeError("temporary failure"), "message-1"]

    await rail.after_tool_call(_context(to="peer", result=ToolOutput(success=True)))
    blocked = _context(to="other")
    await rail.before_tool_call(blocked)
    repeated = _context(to="third")
    await rail.before_tool_call(repeated)

    assert blocked.extra["_skip_tool"] is True
    assert repeated.extra["_skip_tool"] is True
    assert rail._messages.send_message.await_count == 2
    assert rail._leader_notified is True


@pytest.mark.parametrize(
    ("role", "cap", "expected"),
    [
        ("teammate", 2, True),
        ("leader", 2, False),
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
    assert leader_cap == []
