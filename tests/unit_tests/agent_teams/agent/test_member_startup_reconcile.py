# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the leader's round-idle member-startup reconcile.

Registration and startup are separate steps — ``spawn_teammate`` only writes a
DB row — so a round that never walks the startup funnel can leave members
parked at UNSTARTED while the board holds open work. The task-creation path
closes that window as it happens; this reconcile is the backstop behind it,
fired on the leader's round-idle edge, and it has to run *before* the
completion poll that shares the same edge.
"""

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent import AgentCard

LEADER = "leader"


def _agent_with_board(non_terminal: int, started: list[str] | None = None) -> tuple[TeamAgent, list]:
    """Build a leader TeamAgent over a stub backend, recording autostart calls."""
    calls: list = []

    async def count_tasks_terminality(team_name: str) -> tuple[int, int]:
        return 3, non_terminal

    async def autostart_unstarted() -> list[str]:
        calls.append("autostart")
        return list(started or [])

    agent = TeamAgent(AgentCard(name=LEADER))
    agent._configurator.team_backend = SimpleNamespace(
        team_name="t",
        db=SimpleNamespace(task=SimpleNamespace(count_tasks_terminality=count_tasks_terminality)),
        autostart_unstarted=autostart_unstarted,
    )
    return agent, calls


@pytest.mark.asyncio
async def test_reconcile_starts_members_when_the_board_holds_work():
    agent, calls = _agent_with_board(non_terminal=1, started=["dev-1"])

    await agent._reconcile_member_startup()

    assert calls == ["autostart"]


@pytest.mark.asyncio
async def test_reconcile_skips_a_settled_board():
    """No open task means nobody is waiting to be started for anything."""
    agent, calls = _agent_with_board(non_terminal=0)

    await agent._reconcile_member_startup()

    assert calls == []


@pytest.mark.asyncio
async def test_reconcile_is_leader_only():
    agent, calls = _agent_with_board(non_terminal=5)
    # ``role`` reads through the blueprint's runtime context; a bare agent
    # defaults to LEADER, so a teammate has to be stated explicitly.
    agent._configurator._blueprint = SimpleNamespace(ctx=SimpleNamespace(role=TeamRole.TEAMMATE))

    await agent._reconcile_member_startup()

    assert calls == []


@pytest.mark.asyncio
async def test_reconcile_without_a_backend_is_a_noop():
    agent = TeamAgent(AgentCard(name=LEADER))

    await agent._reconcile_member_startup()


@pytest.mark.asyncio
async def test_reconcile_swallows_a_backend_failure():
    """It runs on a teardown-adjacent edge; a failure must not propagate."""

    async def count_tasks_terminality(team_name: str) -> tuple[int, int]:
        raise RuntimeError("database is locked")

    agent = TeamAgent(AgentCard(name=LEADER))
    agent._configurator.team_backend = SimpleNamespace(
        team_name="t",
        db=SimpleNamespace(task=SimpleNamespace(count_tasks_terminality=count_tasks_terminality)),
    )

    await agent._reconcile_member_startup()


@pytest.mark.asyncio
async def test_idle_edge_reconciles_before_polling_for_completion():
    """Order is load-bearing: starting members can put the team back in motion.

    A completion verdict taken first would call a team done at the exact
    moment its members were coming up.
    """
    order: list[str] = []

    async def reconcile() -> None:
        order.append("reconcile")

    async def completion_poll() -> None:
        order.append("completion")

    state = TeamAgentState()
    controller = StreamController(
        blueprint_getter=lambda: SimpleNamespace(member_name=LEADER, role=TeamRole.LEADER),
        state=state,
        resources=SimpleNamespace(),
        status_updater=_noop_status,
        execution_updater=_noop_execution,
        member_startup_reconciler=reconcile,
        request_completion_poll_callback=completion_poll,
    )

    await controller._on_idle_settled()

    assert order == ["reconcile", "completion"]


@pytest.mark.asyncio
async def test_idle_edge_skips_both_callbacks_while_tearing_down():
    """A cleaned team is closing its stream; there is nobody left to start."""
    order: list[str] = []

    async def reconcile() -> None:
        order.append("reconcile")

    state = TeamAgentState()
    state.team_cleaned = True
    controller = StreamController(
        blueprint_getter=lambda: SimpleNamespace(member_name=LEADER, role=TeamRole.LEADER),
        state=state,
        resources=SimpleNamespace(),
        status_updater=_noop_status,
        execution_updater=_noop_execution,
        member_startup_reconciler=reconcile,
    )

    await controller._on_idle_settled()

    assert order == []


async def _noop_status(status: MemberStatus) -> None:
    return None


async def _noop_execution(status: ExecutionStatus) -> None:
    return None
