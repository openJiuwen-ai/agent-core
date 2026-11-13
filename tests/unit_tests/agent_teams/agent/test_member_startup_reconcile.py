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
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.state import TeamAgentState
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.i18n import get_language, set_language
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent import AgentCard

LEADER = "leader"


@pytest.fixture
def leader_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give TeamAgent a stand-in runtime so startup notices are deliverable."""
    monkeypatch.setattr(TeamAgent, "harness", property(lambda self: object()))


def _spawning_backend(agent: TeamAgent, **extra) -> SimpleNamespace:
    """Backend stub whose startup paths spawn through the leader's callback, like TeamBackend."""

    async def startup_member(member_name: str, on_created) -> bool:
        await on_created(member_name)
        return True

    async def startup(on_created, *, include_idle_task_scoped: bool = False) -> list[str]:
        started = []
        for member_name in ("dev-1", "dev-2"):
            await on_created(member_name)
            started.append(member_name)
        return started

    backend = SimpleNamespace(is_leader=True, startup_member=startup_member, startup=startup, **extra)
    agent._configurator.team_backend = backend
    return backend


@pytest.mark.asyncio
@pytest.mark.usefixtures("leader_runtime")
async def test_auto_start_failure_notifies_once_per_episode_and_resets_after_success():
    agent = TeamAgent(AgentCard(name=LEADER))
    _spawning_backend(agent)
    agent._on_teammate_created = AsyncMock(side_effect=[
        RuntimeError("invalid reference: origin/main"),
        RuntimeError("timed out after 30.01s"),
        None,
        RuntimeError("invalid reference: origin/main"),
    ])
    agent.deliver_input = AsyncMock()

    assert not await agent.auto_start_member("dev-1")
    assert not await agent.auto_start_member("dev-1")
    agent.deliver_input.assert_awaited_once()
    assert "origin/main" in agent.deliver_input.await_args.args[0]
    assert "dev-1" in agent.deliver_input.await_args.args[0]
    assert await agent.auto_start_member("dev-1")
    assert not await agent.auto_start_member("dev-1")
    assert agent.deliver_input.await_count == 2


@pytest.mark.asyncio
async def test_auto_start_already_running_does_not_report_failure():
    agent = TeamAgent(AgentCard(name=LEADER))
    agent._configurator.team_backend = SimpleNamespace(
        is_leader=True, startup_member=AsyncMock(return_value=False), recover_member=AsyncMock(return_value=False),
    )
    agent.deliver_input = AsyncMock()
    assert not await agent.auto_start_member("dev-1")
    agent.deliver_input.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("leader_runtime")
async def test_idle_reconcile_failure_names_the_member_without_a_notification_loop():
    """Each retry's error text differs; the open episode still suppresses the notice."""
    agent, _ = _agent_with_board(non_terminal=1)
    attempts = iter(range(1, 10))

    async def autostart_unstarted() -> list[str]:
        await agent._start_teammate_runtime("dev-1")
        return ["dev-1"]

    async def failing_spawn(member_name: str) -> None:
        raise RuntimeError(f"health check timed out after {next(attempts)}s")

    agent.team_backend.autostart_unstarted = autostart_unstarted
    agent._on_teammate_created = failing_spawn
    agent.deliver_input = AsyncMock()

    await agent._reconcile_member_startup()
    await agent._reconcile_member_startup()

    agent.deliver_input.assert_awaited_once()
    assert "dev-1" in agent.deliver_input.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.usefixtures("leader_runtime")
async def test_startup_notification_failure_does_not_escape_best_effort_path():
    agent = TeamAgent(AgentCard(name=LEADER))
    _spawning_backend(agent)
    agent._on_teammate_created = AsyncMock(side_effect=RuntimeError("bad base"))
    agent.deliver_input = AsyncMock(side_effect=RuntimeError("leader input unavailable"))
    assert not await agent.auto_start_member("dev-1")


@pytest.mark.asyncio
@pytest.mark.usefixtures("leader_runtime")
async def test_batch_start_failure_names_the_failed_member():
    agent = TeamAgent(AgentCard(name=LEADER))
    _spawning_backend(agent)

    async def spawn(member_name: str) -> None:
        if member_name == "dev-2":
            raise RuntimeError("bad base")

    agent._on_teammate_created = spawn
    agent.deliver_input = AsyncMock()

    assert await agent.auto_start_all() == []
    assert await agent.auto_start_all() == []

    agent.deliver_input.assert_awaited_once()
    notice = agent.deliver_input.await_args.args[0]
    assert "dev-2" in notice
    assert "dev-1" not in notice


@pytest.mark.asyncio
@pytest.mark.usefixtures("leader_runtime")
async def test_exhausted_restart_is_reported_and_success_reopens_reporting():
    agent = TeamAgent(AgentCard(name=LEADER))
    outcomes = iter([False, True, False])

    async def restart_teammate(member_name: str, *, on_failed) -> bool:
        restarted = next(outcomes)
        if not restarted:
            await on_failed(RuntimeError("invalid reference: origin/main"))
        return restarted

    agent._spawn_manager.restart_teammate = restart_teammate
    agent.deliver_input = AsyncMock()

    assert not await agent._restart_teammate_runtime("dev-1")
    assert await agent._restart_teammate_runtime("dev-1")
    assert not await agent._restart_teammate_runtime("dev-1")

    assert agent.deliver_input.await_count == 2
    assert "dev-1" in agent.deliver_input.await_args.args[0]


@pytest.mark.asyncio
async def test_failure_without_leader_runtime_stays_reportable(monkeypatch: pytest.MonkeyPatch):
    """deliver_input drops input when there is no runtime; that must not close the episode."""
    agent = TeamAgent(AgentCard(name=LEADER))
    agent.deliver_input = AsyncMock()

    await agent._report_member_startup_failure("dev-1", RuntimeError("bad base"))
    agent.deliver_input.assert_not_awaited()

    monkeypatch.setattr(TeamAgent, "harness", property(lambda self: object()))
    await agent._report_member_startup_failure("dev-1", RuntimeError("bad base"))
    agent.deliver_input.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("leader_runtime")
@pytest.mark.parametrize("language,marker", [("cn", "成员启动失败"), ("en", "member startup failed")])
async def test_startup_diagnostics_are_localized(language, marker):
    agent = TeamAgent(AgentCard(name=LEADER))
    agent.deliver_input = AsyncMock()
    previous_language = get_language()
    try:
        set_language(language)
        await agent._report_member_startup_failure("dev-1", RuntimeError("bad base"))
        await agent._report_member_startup_failure("dev-1", RuntimeError("disk full"))
    finally:
        set_language(previous_language)
    agent.deliver_input.assert_awaited_once()
    assert marker in agent.deliver_input.await_args.args[0]
    assert "bad base" in agent.deliver_input.await_args.args[0]


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
