# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for task revisions and staleness (F_129).

Every title / content edit bumps a task's ``revision``; ``observed_revision``
records what its assignee was actually shown. Under scheduled dispatch the
scheduler pushes the latest content to an executor that has not seen it;
under autonomous dispatch the member is told on its next input; both modes
refuse a completion that predates an unseen edit. Subtasks carry the parent
revision they were split from and are reviewed level by level.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.coordination.event_bus import InnerEventMessage, InnerEventType
from openjiuwen.agent_teams.agent.coordination.handlers.task_board import ScheduledTaskBoardHandler, TaskBoardHandler
from openjiuwen.agent_teams.agent.infra import TeamInfra
from openjiuwen.agent_teams.agent.scheduling import TeamScheduler
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.message_template import (
    expand_message,
    revision_getters,
    task_links,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home
from openjiuwen.agent_teams.schema.events import EventMessage, TaskUpdatedEvent, TeamEvent
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.schema.task import REVISION_DIFF_MIN_CHARS, TaskGraphSpec
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import (
    TASK_REVISION_STATE_KEY,
    TaskOutcomeTracker,
    TaskRevisionTracker,
    build_task_event_tracker,
)
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.tools.tool_task import MemberSubtaskUpdateTool, UpdateTaskTool
from openjiuwen.core.single_agent import AgentCard

TEAM = "revision_team"
LEADER = "team_leader"
DEV_1 = "dev-1"
DEV_2 = "dev-2"
DEV_3 = "dev-3"


@pytest_asyncio.fixture
async def db(tmp_path):
    """Scheduled team on an in-memory DB, task bodies spilled under ``tmp_path``."""
    configure_openjiuwen_home(tmp_path)
    token = set_session_id("revision_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM,
            display_name="Revision Team",
            leader_member_name=LEADER,
            dispatch_mode="scheduled",
        )
        for name, role in ((LEADER, "leader"), (DEV_1, "teammate"), (DEV_2, "teammate"), (DEV_3, "teammate")):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                role=role,
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()
        reset_openjiuwen_home()


@pytest_asyncio.fixture
async def bus():
    yield AsyncMock(spec=Messager)


def _mgr(db, bus, name: str, dispatch_mode: str = "scheduled") -> TeamTaskManager:
    return TeamTaskManager(team_name=TEAM, member_name=name, db=db, messager=bus, dispatch_mode=dispatch_mode)


def _tools(db, bus, name: str, *, dispatch_mode: str = "scheduled", depth: int = 2) -> dict:
    backend = TeamBackend(
        team_name=TEAM,
        member_name=name,
        is_leader=name == LEADER,
        db=db,
        messager=bus,
        dispatch_mode=dispatch_mode,
        max_subtask_depth=depth,
    )
    role = "leader" if name == LEADER else "teammate"
    tools = create_team_tools(role=role, agent_team=backend, dispatch_mode=dispatch_mode, lang="cn")
    return {tool.card.name: tool for tool in tools}


class FakeHost:
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
    task_manager = _mgr(db, bus, LEADER)
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


def _sent(message_manager) -> list[tuple[str, dict]]:
    return [
        (call.kwargs["to_member_name"], call.kwargs["meta"]) for call in message_manager.send_message.await_args_list
    ]


def _sent_templates(message_manager) -> list[tuple[str, str, str]]:
    return [(to, meta["template"], meta["refs"]["task"]) for to, meta in _sent(message_manager)]


async def _scan(scheduler) -> None:
    await scheduler.on_event(InnerEventMessage(event_type=InnerEventType.SCHEDULER_SCAN))


async def _deliver(db, bus, member: str, meta: dict) -> str:
    """Render a templated mail the way the member's mailbox drain does, and record what it showed."""

    async def _get_task(task_id: str):
        return await db.task.get_task(task_id)

    async def _get_member(name: str):
        return await db.member.get_member(name, TEAM)

    async def _links(task_id: str):
        return await task_links(db, task_id)

    snapshot, subtasks = revision_getters(db, TEAM)
    expanded = await expand_message(
        SimpleNamespace(content="", meta=__import__("json").dumps(meta)),
        task_getter=_get_task,
        member_getter=_get_member,
        language="cn",
        task_links_getter=_links,
        revision_snapshot_getter=snapshot,
        subtasks_getter=subtasks,
    )
    if expanded.shown_revision is not None:
        await _mgr(db, bus, member).observe_revision(*expanded.shown_revision)
    return expanded.body


async def _start(db, bus, scheduler, task_id: str = "t1", assignee: str = DEV_1, content: str = "v0") -> None:
    spec = TaskGraphSpec(title="build", content=content, task_id=task_id, assignee=assignee)
    assert (await _mgr(db, bus, LEADER).add_graph([spec])).ok
    await scheduler.activate()
    for to, meta in _sent(scheduler._infra.message_manager):
        if meta["refs"]["task"] == task_id:
            await _deliver(db, bus, to, meta)


# ---------------------------------------------------------------------------
# Revision bookkeeping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_edits_bump_the_revision_and_record_change_notes(db, bus):
    tm = _mgr(db, bus, LEADER)
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok

    first = await tm.update_task("t1", content="c1", change_note="scope widened")
    same = await tm.update_task("t1", content="c1")
    second = await tm.update_task("t1", title="a2")

    assert first.data == {"revision": 1, "revised": True}
    assert same.data == {"revision": 1, "revised": False}
    assert second.data["revision"] == 2
    task = await tm.get("t1")
    assert task.revision == 2 and task.observed_revision == 0
    assert '"scope widened"' in task.change_notes


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reassign_reset_and_claim_align_the_seen_revision(db, bus):
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await tm.add_graph([TaskGraphSpec(title="b", content="c0", task_id="t2")])).ok
    await tm.update_task("t1", content="c1")
    await tm.update_task("t2", content="c1")

    assert (await tm.reassign("t1", DEV_2)).ok
    assert (await tm.get("t1")).observed_revision == 1
    assert (await _mgr(db, bus, DEV_3, dispatch_mode="autonomous").claim("t2")).ok
    assert (await tm.get("t2")).observed_revision == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_only_the_assignee_reading_its_task_counts_as_seen(db, bus):
    tm = _mgr(db, bus, LEADER)
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    await tm.update_task("t1", content="c1")

    await _tools(db, bus, DEV_2)["view_task"].invoke({"action": "get", "task_id": "t1"})
    assert (await tm.get("t1")).observed_revision == 0
    await _tools(db, bus, DEV_1)["view_task"].invoke({"action": "get", "task_id": "t1"})
    assert (await tm.get("t1")).observed_revision == 1


# ---------------------------------------------------------------------------
# Completion gate (both modes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("dispatch_mode", ["scheduled", "autonomous"])
async def test_completion_waits_until_the_latest_revision_is_seen(db, bus, dispatch_mode):
    tm = _mgr(db, bus, LEADER, dispatch_mode=dispatch_mode)
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    member = _mgr(db, bus, DEV_1, dispatch_mode=dispatch_mode)
    if dispatch_mode == "scheduled":
        assert (await tm.start_task("t1")).ok
    else:
        assert (await member.db.task.start_task("t1", DEV_1)) is True
    await tm.update_task("t1", content="c1")
    tools = _tools(db, bus, DEV_1, dispatch_mode=dispatch_mode)
    complete = tools["member_complete_task"] if dispatch_mode == "scheduled" else tools["claim_task"]
    args = {"task_id": "t1"} if dispatch_mode == "scheduled" else {"task_id": "t1", "status": "completed"}

    refused = await complete.invoke(args)
    assert not refused.success and "r0 -> r1" in refused.error

    await tools["view_task"].invoke({"action": "get", "task_id": "t1"})
    assert (await complete.invoke(args)).success


# ---------------------------------------------------------------------------
# Scheduled push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_edited_active_task_is_pushed_once_with_the_latest_content(db, bus):
    scheduler, host, mm, tm = _scheduler(db, bus)
    await _start(db, bus, scheduler)
    mm.send_message.reset_mock()

    await tm.update_task("t1", content="v1", change_note="first change")
    await tm.update_task("t1", content="v2", change_note="second change")
    await tm.update_task("t1", content="v3", change_note="third change")
    await _scan(scheduler)
    await _scan(scheduler)

    pushes = [item for item in _sent(mm) if item[1]["template"] == "scheduler_task_revised"]
    assert len(pushes) == 1 and pushes[0][0] == DEV_1
    assert DEV_1 in host.started_members
    body = await _deliver(db, bus, DEV_1, pushes[0][1])
    assert "v3" in body and "r3" in body
    assert body.index("first change") < body.index("second change") < body.index("third change")
    assert (await tm.get("t1")).observed_revision == 3

    scheduler._revision_pushed.clear()
    await _scan(scheduler)
    assert len([item for item in _sent(mm) if item[1]["template"] == "scheduler_task_revised"]) == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_edit_before_start_rides_the_start_handoff(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    leader = _mgr(db, bus, LEADER)
    assert (await leader.add_graph([TaskGraphSpec(title="a", content="v0", task_id="up", assignee=DEV_1)])).ok
    spec = TaskGraphSpec(title="b", content="v0", task_id="down", assignee=DEV_2, depends_on=("up",))
    assert (await leader.add_graph([spec])).ok
    await tm.update_task("down", content="v1")
    await scheduler.activate()

    assert not [item for item in _sent_templates(mm) if item[2] == "down"]
    assert (await _mgr(db, bus, DEV_1).complete("up")).ok
    await _scan(scheduler)
    start = [meta for to, meta in _sent(mm) if meta["refs"]["task"] == "down"]
    assert [meta["template"] for meta in start] == ["scheduler_task_start"]
    assert "v1" in await _deliver(db, bus, DEV_2, start[0])
    assert (await tm.get("down")).observed_revision == 1


@pytest.mark.level1
def test_scheduled_handler_no_longer_steers_on_edits():
    assert ScheduledTaskBoardHandler.EVENT_METHOD_MAP[TeamEvent.TASK_UPDATED] == "on_task_board_event"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_long_body_push_carries_a_diff_against_the_seen_revision(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    base = "\n".join(f"line {index}" for index in range(REVISION_DIFF_MIN_CHARS // 6))
    await _start(db, bus, scheduler, content=base)
    await tm.update_task("t1", content=base.replace("line 7\n", "line seven\n"))
    await _scan(scheduler)

    push = next(meta for _to, meta in _sent(mm) if meta["template"] == "scheduler_task_revised")
    body = await _deliver(db, bus, DEV_1, push)
    assert "```diff" in body and "-line 7" in body and "+line seven" in body


@pytest.mark.asyncio
@pytest.mark.level1
async def test_short_body_push_has_no_diff(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    await _start(db, bus, scheduler)
    await tm.update_task("t1", content="v1")
    await _scan(scheduler)

    push = next(meta for _to, meta in _sent(mm) if meta["template"] == "scheduler_task_revised")
    assert "```diff" not in await _deliver(db, bus, DEV_1, push)


# ---------------------------------------------------------------------------
# Subtasks follow the parent revision
# ---------------------------------------------------------------------------


async def _split(db, bus, scheduler) -> None:
    await _start(db, bus, scheduler, task_id="p")
    create = _tools(db, bus, DEV_1)["create_task"]
    result = await create.invoke(
        {
            "tasks": [
                {"task_id": "s1", "title": "s1", "content": "do s1", "assignee": DEV_2},
                {"task_id": "s2", "title": "s2", "content": "do s2", "assignee": DEV_3},
            ]
        }
    )
    assert result.success, result.error
    await _scan(scheduler)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_parent_edit_marks_subtasks_stale_and_asks_its_owner_to_review(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    await _split(db, bus, scheduler)
    mm.send_message.reset_mock()

    await tm.update_task("p", content="new parent goal", change_note="goal changed")
    await _scan(scheduler)

    review = [(to, meta) for to, meta in _sent(mm) if meta["template"] == "scheduler_subtask_review"]
    assert [to for to, _meta in review] == [DEV_1]
    body = await _deliver(db, bus, DEV_1, review[0][1])
    assert "new parent goal" in body and "goal changed" in body and "需复核" in body
    listed = _tools(db, bus, DEV_1)["view_task"]
    text = listed.render_for_llm(await listed.invoke({"action": "list"}))
    assert "[stale: parent r1 / based on r0]" in text


@pytest.mark.asyncio
@pytest.mark.level0
async def test_owner_revises_confirms_and_cancels_subtasks(db, bus):
    scheduler, _host, mm, tm = _scheduler(db, bus)
    await _split(db, bus, scheduler)
    await tm.update_task("p", content="new parent goal")
    update = _tools(db, bus, DEV_1)["update_task"]
    assert isinstance(update, MemberSubtaskUpdateTool)
    mm.send_message.reset_mock()

    revised = await update.invoke({"task_id": "s1", "content": "do s1 the new way", "change_note": "follow parent"})
    confirmed = await update.invoke({"task_id": "s2", "confirm": True})
    await _scan(scheduler)

    assert revised.success and confirmed.success
    s1, s2 = await tm.get("s1"), await tm.get("s2")
    assert (s1.revision, s1.parent_revision) == (1, 1)
    assert (s2.revision, s2.parent_revision) == (0, 1)
    assert (DEV_2, "scheduler_task_revised", "s1") in _sent_templates(mm)
    assert not [item for item in _sent_templates(mm) if item[2] == "s2"]

    assert (await update.invoke({"task_id": "s2", "status": "cancelled"})).success
    assert (await tm.get("s2")).status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
@pytest.mark.level1
async def test_subtask_update_is_limited_to_the_parent_owner(db, bus):
    scheduler, _host, _mm, _tm = _scheduler(db, bus)
    await _split(db, bus, scheduler)

    foreign = await _tools(db, bus, DEV_2)["update_task"].invoke({"task_id": "s2", "confirm": True})
    top_level = await _tools(db, bus, DEV_1)["update_task"].invoke({"task_id": "p", "confirm": True})
    stray = await _tools(db, bus, DEV_1)["update_task"].invoke({"task_id": "s1", "assignee": DEV_3})
    note_only = await _tools(db, bus, DEV_1)["update_task"].invoke({"task_id": "s1", "change_note": "x"})

    assert not foreign.success and not top_level.success and not stray.success and not note_only.success


@pytest.mark.asyncio
@pytest.mark.level1
async def test_leader_edit_reports_revision_and_stale_subtasks(db, bus):
    scheduler, _host, _mm, _tm = _scheduler(db, bus)
    await _split(db, bus, scheduler)
    update = _tools(db, bus, LEADER)["update_task"]
    assert isinstance(update, UpdateTaskTool)

    result = await update.invoke({"task_id": "p", "content": "new goal", "change_note": "pivot"})
    text = update.render_for_llm(result)

    assert "now r1" in text and "#s1" in text and "#s2" in text
    assert not (await update.invoke({"task_id": "p", "change_note": "alone"})).success


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_update_task_follows_the_subtask_gate(db, bus):
    assert "update_task" not in _tools(db, bus, DEV_1, depth=0)
    assert isinstance(_tools(db, bus, DEV_1)["update_task"], MemberSubtaskUpdateTool)


# ---------------------------------------------------------------------------
# Autonomous next-input notice
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self, session_id: str = "revision_session") -> None:
        self.state: dict = {}
        self.session_id = session_id

    def get_session_id(self) -> str:
        return self.session_id

    def get_state(self, key: str):
        return self.state.get(key)

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_autonomous_member_is_told_of_a_missed_edit_once(db, bus):
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await tm.add_graph([TaskGraphSpec(title="b", content="c0", task_id="t2", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    await tm.update_task("t1", content="c1", change_note="tightened criteria")
    await tm.update_task("t2", content="c1", change_note="not started yet")
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    session = _Session()

    notice = await tracker.claim(session)
    text = notice.text if notice else ""
    assert 'kind="task-revised"' in text
    assert "t2" not in text and "not started yet" not in text
    assert "r0 → r1" in text and "tightened criteria" in text
    assert await tracker.claim(session) is None

    await tm.update_task("t1", content="c2")
    assert "r0 → r2" in (await tracker.claim(session)).text


@pytest.mark.level1
def test_each_mode_gets_its_own_next_input_tracker():
    backend = SimpleNamespace(task_manager=None)
    assert isinstance(
        build_task_event_tracker("scheduled", team_backend=backend, member_name=DEV_1), TaskOutcomeTracker
    )
    assert isinstance(
        build_task_event_tracker("autonomous", team_backend=backend, member_name=DEV_1), TaskRevisionTracker
    )
    assert build_task_event_tracker("autonomous", team_backend=None, member_name=DEV_1) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_an_edit_is_claimed_once_across_paths_and_restarts(db, bus):
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    await tm.update_task("t1", content="c1")
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    session = _Session()

    assert await tracker.claim(None) is None
    notice = await tracker.claim(session)
    assert notice is not None and notice.entries == ("t1@1",)
    # Another path (the rail on the next input) finds nothing left to say.
    assert await tracker.claim(session) is None
    # A failed delivery gives that claim back, whatever was claimed since.
    tracker.release(notice)
    assert await tracker.claim(session) is not None
    await tracker.persist(session)
    assert session.state[TASK_REVISION_STATE_KEY] == ["t1@1"]

    # A restarted process reads the checkpointed baseline before its first claim.
    restarted = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    assert await restarted.claim(session) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_the_baseline_belongs_to_one_session(db, bus):
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    await tm.update_task("t1", content="c1")
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    first, second = _Session("session-a"), _Session("session-b")

    assert await tracker.claim(first) is not None
    await tracker.persist(first)
    # The same id in another session is a different task: it is announced there.
    assert await tracker.claim(second) is not None
    await tracker.persist(second)
    assert second.state[TASK_REVISION_STATE_KEY] == ["t1@1"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_rolled_back_session_state_is_reloaded(db, bus):
    """Same session id, older state on a fresh session object: the rolled-back edit is told again."""
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    live = _Session()
    await tm.update_task("t1", content="c1")
    assert await tracker.claim(live) is not None
    await tracker.persist(live)
    restored = _Session()
    restored.state = dict(live.state)
    await tm.update_task("t1", content="c2")
    assert await tracker.claim(live) is not None
    await tracker.persist(live)

    # The checkpoint taken before the r2 notice comes back on a new session object.
    notice = await tracker.claim(restored)
    assert notice is not None and notice.entries == ("t1@2",)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_the_bound_session_is_not_reread(db, bus):
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    await tm.update_task("t1", content="c1")
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    session = _Session()

    assert await tracker.claim(session) is not None
    # A stale write to the same object is not read back: the in-memory baseline stays authoritative.
    session.state[TASK_REVISION_STATE_KEY] = []
    assert await tracker.claim(session) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_released_session_is_not_kept_alive(db, bus):
    import gc
    import weakref

    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    await tm.update_task("t1", content="c1")
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    session = _Session()
    assert await tracker.claim(session) is not None
    await tracker.persist(session)
    probe = weakref.ref(session)

    del session
    gc.collect()

    assert probe() is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_an_unpersisted_claim_carries_into_the_next_cycle_of_the_same_session(db, bus):
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    await tm.update_task("t1", content="c1")
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)

    class _FailingCommit(_Session):
        async def commit(self) -> None:
            raise RuntimeError("checkpoint store down")

    first = _FailingCommit()
    assert await tracker.claim(first) is not None
    await tracker.persist(first)  # swallowed: logged, entry stays pending

    next_cycle = _Session()  # same session id, fresh object, nothing stored
    assert await tracker.claim(next_cycle) is None
    await tracker.persist(next_cycle)
    assert next_cycle.state[TASK_REVISION_STATE_KEY] == ["t1@1"]

    # Another session does not inherit it.
    other = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    failing = _FailingCommit()
    assert await other.claim(failing) is not None
    await other.persist(failing)
    assert await other.claim(_Session("another-session")) is not None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_only_the_newest_revision_of_a_task_is_kept(db, bus):
    tm = _mgr(db, bus, LEADER, dispatch_mode="autonomous")
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    assert (await db.task.start_task("t1", DEV_1)) is True
    backend = SimpleNamespace(task_manager=_mgr(db, bus, DEV_1, dispatch_mode="autonomous"))
    tracker = TaskRevisionTracker(team_backend=backend, member_name=DEV_1)
    session = _Session()

    for content in ("c1", "c2", "c3"):
        await tm.update_task("t1", content=content)
        assert await tracker.claim(session) is not None
    await tracker.persist(session)

    assert session.state[TASK_REVISION_STATE_KEY] == ["t1@3"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_task_updated_for_self_only_asks_the_tracker_to_speak():
    host = SimpleNamespace(announce_task_revisions=AsyncMock(), deliver_input=AsyncMock())
    handler = TaskBoardHandler(
        host,
        SimpleNamespace(member_name=DEV_1, role=TeamRole.TEAMMATE),
        SimpleNamespace(task_manager=object()),
        SimpleNamespace(resume_polls=AsyncMock()),
    )
    event = EventMessage.from_event(TaskUpdatedEvent(team_name=TEAM, member_name=DEV_1, task_id="t1", revision=1))

    await handler.on_task_updated(event)

    host.announce_task_revisions.assert_awaited_once()
    host.deliver_input.assert_not_awaited()


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("dispatch_mode", ["scheduled", "autonomous"])
async def test_a_human_assignee_is_not_held_by_the_completion_gate(db, bus, dispatch_mode):
    await db.member.create_member(
        member_name="human-1",
        team_name=TEAM,
        display_name="human-1",
        agent_card=AgentCard().model_dump_json(),
        status="READY",
        role=TeamRole.HUMAN_AGENT.value,
        mode=MemberMode.BUILD_MODE.value,
    )
    tm = _mgr(db, bus, LEADER, dispatch_mode=dispatch_mode)
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee="human-1")])).ok
    await tm.update_task("t1", content="c1")
    # Autonomous dispatch starts a live human's task on assignment.
    if (await tm.get("t1")).status == TaskStatus.PENDING.value:
        assert (await db.task.start_task("t1", "human-1")) is True
    assert (await tm.get("t1")).observed_revision < 1
    tools = _tools(db, bus, "human-1", dispatch_mode=dispatch_mode)
    complete = tools["member_complete_task"] if dispatch_mode == "scheduled" else tools["claim_task"]
    args = {"task_id": "t1"} if dispatch_mode == "scheduled" else {"task_id": "t1", "status": "completed"}

    result = await complete.invoke(args)
    assert result.success, result.error


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_change_note_on_an_unchanged_body_is_refused(db, bus):
    tm = _mgr(db, bus, LEADER)
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok

    result = await _tools(db, bus, LEADER)["update_task"].invoke(
        {"task_id": "t1", "content": "c0", "change_note": "please do X first"}
    )
    assert not result.success and "send_message" in result.error
    task = await tm.get("t1")
    assert task.revision == 0 and "please do X first" not in (task.change_notes or "")


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize(
    ("dispatch_mode", "started", "expected"),
    [
        ("scheduled", True, "sent to dev-1 with the latest content"),
        ("scheduled", False, "has not started it yet"),
        ("autonomous", True, "is told to re-read"),
        ("autonomous", False, "has not started it yet"),
    ],
)
async def test_edit_result_says_how_the_revision_reaches_the_assignee(db, bus, dispatch_mode, started, expected):
    tm = _mgr(db, bus, LEADER, dispatch_mode=dispatch_mode)
    assert (await tm.add_graph([TaskGraphSpec(title="a", content="c0", task_id="t1", assignee=DEV_1)])).ok
    if started:
        assert (await db.task.start_task("t1", DEV_1)) is True
    tool = _tools(db, bus, LEADER, dispatch_mode=dispatch_mode)["update_task"]

    output = await tool.invoke({"task_id": "t1", "content": "c1"})
    assert output.success, output.error
    rendered = tool.render_for_llm(output)
    assert expected in rendered
    assert "sent to" not in rendered or expected.startswith("sent to")


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_filtered_list_still_marks_subtasks_stale_against_an_unlisted_parent(db, bus):
    tm = _mgr(db, bus, LEADER)
    assert (await tm.add_graph([TaskGraphSpec(title="p", content="c0", task_id="p1", assignee=DEV_1)])).ok
    assert (await tm.start_task("p1")).ok
    sub_spec = TaskGraphSpec(title="s", content="c0", task_id="s1", assignee=DEV_2, parent_task_id="p1")
    assert (await _mgr(db, bus, DEV_1).add_graph([sub_spec])).ok
    await tm.update_task("p1", content="c1")

    listed = await tm.list_tasks_with_deps(status=TaskStatus.PENDING.value)
    summary = next(item for item in listed.tasks if item.task_id == "s1")
    assert summary.stale_against == (1, 0)
    assert "p1" not in {item.task_id for item in listed.tasks}
