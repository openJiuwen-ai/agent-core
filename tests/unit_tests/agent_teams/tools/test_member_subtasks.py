# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for scheduled member subtasks (F_128).

A scheduled member splits its executing task with ``create_task``: the
subtasks land, the parent depends on each of them and parks at ``BLOCKED``,
and the scheduler resumes the parent once every subtask is over. One backend
gate (``max_subtask_depth``) drives the tool, the scheduled dispatch prompts
and the leader's disclosure; these tests pin the tool contract, the
suspend / resume cycle through the real scheduler, the leader-side cancel and
reassign rules, and the prompt gating.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.agent.scheduling import TeamScheduler
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.prompts.sections import (
    build_leader_policy_disclosure,
    build_team_dispatch_section,
)
from openjiuwen.agent_teams.schema.blueprint import LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.schema.task import TaskGraphSpec
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.tools.tool_task import MemberSubtaskCreateTool, ScheduledTaskCreateTool
from openjiuwen.core.single_agent import AgentCard

TEAM = "subtask_team"
LEADER = "team_leader"
DEV_1 = "dev-1"
DEV_2 = "dev-2"
PLANNER = "planner"
HUMAN = "human-1"


@pytest_asyncio.fixture
async def db():
    """In-memory scheduled team: leader, two builders, one planner, one human."""
    token = set_session_id("subtask_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM,
            display_name="Subtask Team",
            leader_member_name=LEADER,
            dispatch_mode="scheduled",
        )
        members = [
            (LEADER, "leader", MemberMode.BUILD_MODE),
            (DEV_1, "teammate", MemberMode.BUILD_MODE),
            (DEV_2, "teammate", MemberMode.BUILD_MODE),
            (PLANNER, "teammate", MemberMode.PLAN_MODE),
            (HUMAN, "human_agent", MemberMode.BUILD_MODE),
        ]
        for name, role, mode in members:
            await database.member.create_member(
                member_name=name,
                team_name=TEAM,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                role=role,
                mode=mode.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


@pytest_asyncio.fixture
async def bus():
    yield AsyncMock(spec=Messager)


def _backend(db, bus, member_name: str, *, is_leader: bool = False, depth: int = 2) -> TeamBackend:
    return TeamBackend(
        team_name=TEAM,
        member_name=member_name,
        is_leader=is_leader,
        db=db,
        messager=bus,
        dispatch_mode="scheduled",
        max_subtask_depth=depth,
    )


def _tools(backend: TeamBackend, *, role: str = "teammate", dispatch_mode: str = "scheduled", lang: str = "cn"):
    return {
        tool.card.name: tool
        for tool in create_team_tools(role=role, agent_team=backend, dispatch_mode=dispatch_mode, lang=lang)
    }


def _create_tool(db, bus, member_name: str = DEV_1, *, depth: int = 2) -> MemberSubtaskCreateTool:
    return _tools(_backend(db, bus, member_name, depth=depth))["create_task"]


class FakeHost:
    """Records the scheduler's leader inputs and lazy member starts."""

    def __init__(self):
        self.leader_inputs: list[str] = []
        self.started_members: list[str] = []
        self.harness = SimpleNamespace(find_rails=lambda _rail_type: [])

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        self.leader_inputs.append(str(content))

    async def auto_start_member(self, member_name: str) -> bool:
        self.started_members.append(member_name)
        return True


def _scheduler(db, bus):
    task_manager = TeamTaskManager(team_name=TEAM, member_name=LEADER, db=db, messager=bus, dispatch_mode="scheduled")
    message_manager = AsyncMock()
    message_manager.member_name = LEADER
    message_manager.send_message = AsyncMock(return_value="mid-1")
    message_manager.get_messages = AsyncMock(return_value=[])
    infra = TeamInfra()
    infra.task_manager = task_manager
    infra.message_manager = message_manager
    infra.team_backend = AsyncMock(team_name=TEAM)
    spec = SimpleNamespace(team_name=TEAM, default_max_review_rounds=3, review_stall_timeout=1800, agents=None)
    host = FakeHost()
    scheduler = TeamScheduler(host, blueprint=SimpleNamespace(spec=spec, team_name=TEAM), infra=infra)
    return scheduler, host, message_manager, task_manager


def _handoffs(message_manager) -> list[tuple[str, str, str]]:
    """(recipient, template, task_id) of every leader-identity handoff sent."""
    return [
        (call.kwargs["to_member_name"], call.kwargs["meta"]["template"], call.kwargs["meta"]["refs"]["task"])
        for call in message_manager.send_message.await_args_list
    ]


async def _scan(scheduler) -> None:
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))


def _member_mgr(db, bus, name: str) -> TeamTaskManager:
    return TeamTaskManager(team_name=TEAM, member_name=name, db=db, messager=bus, dispatch_mode="scheduled")


async def _start_parent(db, bus, scheduler, *, task_id: str = "parent", assignee: str = DEV_1) -> None:
    """Leader creates a task, the scheduler starts it."""
    leader_tm = _member_mgr(db, bus, LEADER)
    spec = TaskGraphSpec(title="build", content="c", task_id=task_id, assignee=assignee)
    assert (await leader_tm.add_graph([spec])).ok
    await scheduler.activate()


def _subtask(task_id: str, assignee: str, **extra) -> dict:
    return {"task_id": task_id, "title": task_id, "content": f"do {task_id}", "assignee": assignee, **extra}


# ---------------------------------------------------------------------------
# Tool surface and gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_gets_create_task_only_when_subtasks_are_enabled(db, bus):
    assert "create_task" not in _tools(_backend(db, bus, DEV_1, depth=0))
    tool = _tools(_backend(db, bus, DEV_1, depth=2))["create_task"]
    assert isinstance(tool, MemberSubtaskCreateTool)
    props = tool.card.input_params["properties"]["tasks"]["items"]["properties"]
    assert "assignee" in props
    for absent in ("member", "reviewer", "max_review_rounds", "depended_by", "parent_task_id"):
        assert absent not in props


@pytest.mark.asyncio
@pytest.mark.level1
async def test_leader_and_autonomous_member_are_unaffected(db, bus):
    leader_tools = _tools(_backend(db, bus, LEADER, is_leader=True, depth=0), role="leader")
    assert isinstance(leader_tools["create_task"], ScheduledTaskCreateTool)
    autonomous = TeamBackend(
        team_name=TEAM, member_name=DEV_1, is_leader=False, db=db, messager=bus, max_subtask_depth=2
    )
    assert "create_task" not in _tools(autonomous, dispatch_mode="autonomous")


@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.level1
def test_subtask_description_renders_without_slots(lang):
    tool = MemberSubtaskCreateTool(SimpleNamespace(task_manager=None), _translator(lang))
    assert tool.card.description
    assert "{{" not in tool.card.description


def _translator(lang: str):
    from openjiuwen.agent_teams.tools.locales import make_translator

    return make_translator(lang)


# ---------------------------------------------------------------------------
# Splitting: suspend in one transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_split_suspends_parent_and_links_subtasks(db, bus):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)

    result = await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2), _subtask("s2", DEV_1)]})

    assert result.success, result.error
    parent = await tm.get("parent")
    assert parent.status == TaskStatus.BLOCKED.value
    for sub_id in ("s1", "s2"):
        sub = await tm.get(sub_id)
        assert sub.parent_task_id == "parent"
        assert sub.created_by == DEV_1
    deps = {dep.depends_on_task_id for dep in await tm.get_dependencies("parent")}
    assert deps == {"s1", "s2"}
    rendered = _create_tool(db, bus).render_for_llm(result)
    assert "parent" in rendered and "End this turn" in rendered


@pytest.mark.asyncio
@pytest.mark.level1
async def test_split_without_an_executing_task_is_rejected(db, bus):
    result = await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})
    assert not result.success
    assert "no in-progress task" in result.error


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize(
    "extra",
    [
        {"member": {"member_name": "x"}},
        {"reviewer": [{"type": "verifier"}]},
        {"max_review_rounds": 2},
        {"depended_by": ["parent"]},
        {"parent_task_id": "other"},
        {"member": {}},
        {"reviewer": {}},
        {"max_review_rounds": False},
    ],
)
async def test_split_rejects_leader_only_fields(db, bus, extra):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)

    result = await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2, **extra)]})

    assert not result.success
    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value
    assert await tm.get("s1") is None


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize("assignee", [LEADER, HUMAN, "nobody", ""])
async def test_split_rejects_invalid_owners(db, bus, assignee):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)

    result = await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", assignee)]})

    assert not result.success
    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value


@pytest.mark.asyncio
@pytest.mark.level1
async def test_split_rolls_back_when_the_graph_fails(db, bus):
    """A cycle through the parent rejects the whole batch, parent untouched."""
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)

    result = await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2, depends_on=["parent"])]})

    assert not result.success
    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value
    assert await tm.get("s1") is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_split_depth_is_capped(db, bus):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus, depth=1).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    await _scan(scheduler)
    assert (await tm.get("s1")).status == TaskStatus.IN_PROGRESS.value

    result = await _create_tool(db, bus, DEV_2, depth=1).invoke({"tasks": [_subtask("s1a", DEV_2)]})

    assert not result.success
    assert "depth 1" in result.error
    assert (await tm.get("s1")).status == TaskStatus.IN_PROGRESS.value


# ---------------------------------------------------------------------------
# Resume through the scheduler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_parent_resumes_with_resume_handoff_after_subtasks(db, bus):
    scheduler, host, mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_1), _subtask("s2", DEV_2)]})).success

    await _scan(scheduler)
    # The parent freed dev-1's slot, so its own subtask starts.
    assert (await tm.get("s1")).status == TaskStatus.IN_PROGRESS.value
    assert (await tm.get("s2")).status == TaskStatus.IN_PROGRESS.value

    assert (await _member_mgr(db, bus, DEV_1).complete("s1", summary="half one")).ok
    await _scan(scheduler)
    assert (await tm.get("parent")).status == TaskStatus.BLOCKED.value

    assert (await _member_mgr(db, bus, DEV_2).complete("s2", summary="half two")).ok
    await _scan(scheduler)

    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value
    assert (DEV_1, "scheduler_task_resume", "parent") in _handoffs(mm)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cancelled_subtask_still_lets_the_parent_resume(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success

    assert await tm.cancel("s1") is not None
    await _scan(scheduler)

    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value
    assert (DEV_1, "scheduler_task_resume", "parent") in _handoffs(mm)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_plan_mode_parent_resumes_past_the_plan_gate(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    leader_tm = _member_mgr(db, bus, LEADER)
    assert (await leader_tm.add_graph([TaskGraphSpec(title="p", content="c", task_id="parent", assignee=PLANNER)])).ok
    # The plan was approved on first start; the gate itself is not under test.
    assert await tm.db.task.start_task("parent", PLANNER, to_status=TaskStatus.IN_PROGRESS)
    await scheduler.activate()
    assert (await _create_tool(db, bus, PLANNER).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    await _scan(scheduler)

    assert (await _member_mgr(db, bus, DEV_2).complete("s1")).ok
    await _scan(scheduler)

    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value
    assert (PLANNER, "scheduler_task_resume", "parent") in _handoffs(mm)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resumed_parent_starts_before_fresh_work(db, bus):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    leader_tm = _member_mgr(db, bus, LEADER)
    # dev-1 picks up fresh leader work while its parent is parked.
    assert (await leader_tm.add_graph([TaskGraphSpec(title="fresh", content="c", task_id="fresh", assignee=DEV_1)])).ok
    await _scan(scheduler)
    assert (await tm.get("fresh")).status == TaskStatus.IN_PROGRESS.value
    # A second fresh task queues behind it, as does the parent once unblocked.
    assert (await leader_tm.add_graph([TaskGraphSpec(title="later", content="c", task_id="later", assignee=DEV_1)])).ok
    assert (await _member_mgr(db, bus, DEV_2).complete("s1")).ok
    await _scan(scheduler)
    assert (await tm.get("parent")).status == TaskStatus.PENDING.value

    assert (await _member_mgr(db, bus, DEV_1).complete("fresh")).ok
    await _scan(scheduler)

    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value
    assert (await tm.get("later")).status == TaskStatus.PENDING.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_members_splitting_toward_each_other_do_not_deadlock(db, bus):
    """dev-1 and dev-2 each hand the other a subtask; everything still finishes."""
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    leader_tm = _member_mgr(db, bus, LEADER)
    assert (
        await leader_tm.add_graph(
            [
                TaskGraphSpec(title="a", content="c", task_id="a", assignee=DEV_1),
                TaskGraphSpec(title="b", content="c", task_id="b", assignee=DEV_2),
            ]
        )
    ).ok
    await scheduler.activate()
    assert (await _create_tool(db, bus, DEV_1).invoke({"tasks": [_subtask("a1", DEV_2)]})).success
    assert (await _create_tool(db, bus, DEV_2).invoke({"tasks": [_subtask("b1", DEV_1)]})).success
    await _scan(scheduler)
    assert (await tm.get("a1")).status == TaskStatus.IN_PROGRESS.value
    assert (await tm.get("b1")).status == TaskStatus.IN_PROGRESS.value

    assert (await _member_mgr(db, bus, DEV_2).complete("a1")).ok
    assert (await _member_mgr(db, bus, DEV_1).complete("b1")).ok
    await _scan(scheduler)
    assert (await _member_mgr(db, bus, DEV_1).complete("a")).ok
    assert (await _member_mgr(db, bus, DEV_2).complete("b")).ok

    statuses = {task.status for task in await tm.list_tasks()}
    assert statuses == {TaskStatus.COMPLETED.value}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_hears_about_the_parent_only(db, bus):
    scheduler, host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    await _scan(scheduler)

    assert (await _member_mgr(db, bus, DEV_2).complete("s1")).ok
    await scheduler._digest_task_done(tm, await tm.get("s1"), verified=False)
    assert host.leader_inputs == []

    await _scan(scheduler)
    assert (await _member_mgr(db, bus, DEV_1).complete("parent")).ok
    await scheduler._digest_task_done(tm, await tm.get("parent"), verified=False)
    assert any("parent" in text for text in host.leader_inputs)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_resumed_parent_reassigned_hands_the_new_owner_a_fresh_start(db, bus):
    """The resume belongs to the owner that suspended the task, not to the task."""
    scheduler, _host, mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    await _scan(scheduler)
    assert (await _member_mgr(db, bus, DEV_2).complete("s1")).ok
    await _scan(scheduler)
    assert (await tm.get("parent")).status == TaskStatus.IN_PROGRESS.value

    assert (await tm.reassign("parent", DEV_2)).ok
    await _scan(scheduler)

    assert (DEV_2, "scheduler_task_start", "parent") in _handoffs(mm)
    assert (DEV_2, "scheduler_task_resume", "parent") not in _handoffs(mm)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_parent_reassigned_before_resuming_goes_through_the_plan_gate(db, bus):
    """A pending resume handed to someone else is cleared: the new owner plans first."""
    scheduler, _host, mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    await _scan(scheduler)
    # dev-1 is busy with fresh work, so the unblocked parent waits at PENDING.
    leader_tm = _member_mgr(db, bus, LEADER)
    assert (await leader_tm.add_graph([TaskGraphSpec(title="f", content="c", task_id="fresh", assignee=DEV_1)])).ok
    await _scan(scheduler)
    assert (await _member_mgr(db, bus, DEV_2).complete("s1")).ok
    await _scan(scheduler)
    parent = await tm.get("parent")
    assert parent.status == TaskStatus.PENDING.value and parent.resume_pending

    assert (await tm.reassign("parent", PLANNER)).ok
    assert not (await tm.get("parent")).resume_pending
    await _scan(scheduler)

    assert (await tm.get("parent")).status == TaskStatus.PLANNING.value
    assert (PLANNER, "scheduler_task_start_plan", "parent") in _handoffs(mm)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reset_parent_starts_over_for_its_next_owner(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    await _scan(scheduler)
    assert (await _member_mgr(db, bus, DEV_2).complete("s1")).ok
    await _scan(scheduler)

    assert (await tm.reset("parent")).ok
    assert (await tm.assign("parent", PLANNER)).ok
    await _scan(scheduler)

    assert (await tm.get("parent")).status == TaskStatus.PLANNING.value
    assert (PLANNER, "scheduler_task_resume", "parent") not in _handoffs(mm)


# ---------------------------------------------------------------------------
# Leader operations on parents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cancelling_a_parent_cancels_its_open_subtasks(db, bus):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2), _subtask("s2", DEV_1)]})).success
    await _scan(scheduler)
    assert (await _member_mgr(db, bus, DEV_2).complete("s1")).ok

    assert await tm.cancel("parent") is not None

    assert (await tm.get("parent")).status == TaskStatus.CANCELLED.value
    assert (await tm.get("s1")).status == TaskStatus.COMPLETED.value
    assert (await tm.get("s2")).status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cascade_cancel_reports_the_drained_board_once(db, bus):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2), _subtask("s2", DEV_1)]})).success
    bus.publish.reset_mock()

    assert await tm.cancel("parent") is not None

    drained = [
        call for call in bus.publish.await_args_list if call.kwargs["message"].event_type == TeamEvent.TASK_LIST_DRAINED
    ]
    assert len(drained) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_suspended_parent_cannot_be_reassigned(db, bus):
    scheduler, _host, _mm, tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success

    result = await tm.reassign("parent", DEV_2)

    assert not result.ok
    assert "suspended" in result.reason
    assert (await tm.get("parent")).assignee == DEV_1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_ordinary_blocked_task_stays_reassignable(db, bus):
    leader_tm = _member_mgr(db, bus, LEADER)
    assert (
        await leader_tm.add_graph(
            [
                TaskGraphSpec(title="up", content="c", task_id="up", assignee=DEV_1),
                TaskGraphSpec(title="down", content="c", task_id="down", assignee=DEV_1, depends_on=("up",)),
            ]
        )
    ).ok
    assert (await leader_tm.get("down")).status == TaskStatus.BLOCKED.value

    assert (await leader_tm.reassign("down", DEV_2)).ok


@pytest.mark.asyncio
@pytest.mark.level1
async def test_view_task_shows_parent_and_subtasks(db, bus):
    scheduler, _host, _mm, _tm = _scheduler(db, bus)
    await _start_parent(db, bus, scheduler)
    assert (await _create_tool(db, bus).invoke({"tasks": [_subtask("s1", DEV_2)]})).success
    view = _tools(_backend(db, bus, DEV_1))["view_task"]

    listed = view.render_for_llm(await view.invoke({"action": "list"}))
    detail = view.render_for_llm(await view.invoke({"action": "get", "task_id": "parent"}))

    assert "[subtask of #parent]" in listed
    assert "Subtasks: #s1" in detail


# ---------------------------------------------------------------------------
# Prompt gating and spec plumbing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize("role", [TeamRole.LEADER, TeamRole.TEAMMATE])
@pytest.mark.level1
def test_dispatch_section_carries_the_subtask_rule_only_when_enabled(lang, role):
    off = build_team_dispatch_section(role=role, dispatch_mode="scheduled", member_subtasks=False, language=lang)
    on = build_team_dispatch_section(role=role, dispatch_mode="scheduled", member_subtasks=True, language=lang)
    off_text, on_text = off.content[lang], on.content[lang]
    assert "{{" not in off_text and "{{" not in on_text
    slug = "leader" if role == TeamRole.LEADER else "teammate"
    rule = load_template(f"dispatch_scheduled_{slug}_subtasks", lang).content.strip()
    assert rule not in off_text
    # The rule joins the surrounding list as its own item — no blank line before it.
    assert f"\n{rule}" in on_text and f"\n\n{rule}" not in on_text
    assert "\n\n\n" not in off_text and "\n\n\n" not in on_text


@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.level1
def test_leader_disclosure_follows_the_gate(lang):
    off = build_leader_policy_disclosure(dispatch_mode="scheduled", member_subtasks=False, language=lang)
    on = build_leader_policy_disclosure(dispatch_mode="scheduled", member_subtasks=True, language=lang)
    assert "subtask of" in on and "subtask of" not in off


@pytest.mark.level1
def test_spec_defaults_off_and_rejects_negative_depth():
    assert TeamAgentSpec(team_name="t", agents={}).max_subtask_depth == 0
    spec = TeamAgentSpec(
        team_name="t",
        leader=LeaderSpec(member_name=LEADER),
        agents={},
        dispatch_mode="scheduled",
        max_subtask_depth=2,
    )
    assert spec.max_subtask_depth == 2
    with pytest.raises(ValueError):
        TeamAgentSpec(team_name="t", agents={}, max_subtask_depth=-1)


@pytest.mark.level1
def test_descriptor_carries_the_depth_through_json():
    descriptor = TeamJoinDescriptor(
        session_id="s",
        team_name=TEAM,
        member_name=DEV_1,
        scope="member",
        dispatch_mode="scheduled",
        max_subtask_depth=2,
    )
    assert TeamJoinDescriptor.model_validate_json(descriptor.to_json()).max_subtask_depth == 2
