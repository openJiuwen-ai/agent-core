# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for dispatch-mode tool variants.

A *variant* keeps ``ToolCard.id`` / ``name`` and swaps schema, description,
and behaviour; selection happens while ``create_team_tools`` builds its tool
dict, never inside ``invoke``. These tests pin the three things that must
hold: which tools get registered, that a variant's schema *is* its contract,
and that ``create_task(assignee=...)`` lands atomically.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.status import MemberMode, TaskStatus
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.tools.tool_message import ReportToLeaderTool, SendMessageTool
from openjiuwen.agent_teams.tools.tool_task import ScheduledTaskCreateTool, TaskCreateTool
from openjiuwen.core.single_agent import AgentCard

TEAM_NAME = "variant_team"
LEADER_NAME = "team_leader"
DEV_1 = "dev-1"
DEV_2 = "dev-2"


@pytest_asyncio.fixture
async def db():
    """In-memory team DB with a leader and two teammates."""
    token = set_session_id("variant_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM_NAME,
            display_name="Variant Team",
            leader_member_name=LEADER_NAME,
        )
        for name in (LEADER_NAME, DEV_1, DEV_2):
            await database.member.create_member(
                member_name=name,
                team_name=TEAM_NAME,
                display_name=name,
                agent_card=AgentCard().model_dump_json(),
                status="READY",
                mode=MemberMode.BUILD_MODE.value,
            )
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _backend(db, member_name: str, is_leader: bool) -> TeamBackend:
    # No leader_member_name passed: a member resolves it from the team_info DB
    # row (the source of truth), exercising resolve_leader_member_name.
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=member_name,
        is_leader=is_leader,
        db=db,
        messager=AsyncMock(spec=Messager),
    )


def _tool_names(tools) -> set[str]:
    return {tool.card.name for tool in tools}


def _by_name(tools, name: str):
    return next(tool for tool in tools if tool.card.name == name)


# ---------------------------------------------------------------------------
# Registration differs by dispatch mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_autonomous_member_gets_claim_task(db):
    """Autonomous members claim from the board."""
    tools = create_team_tools(role="teammate", agent_team=_backend(db, DEV_1, False))
    names = _tool_names(tools)
    assert "claim_task" in names
    assert "member_complete_task" not in names


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_member_swaps_claim_for_complete(db):
    """Scheduled members never claim; they complete what the leader assigned."""
    tools = create_team_tools(
        role="teammate",
        agent_team=_backend(db, DEV_1, False),
        dispatch_mode="scheduled",
    )
    names = _tool_names(tools)
    assert "claim_task" not in names
    assert "member_complete_task" in names


@pytest.mark.asyncio
@pytest.mark.level0
async def test_unknown_dispatch_mode_fails_loudly(db):
    """An unknown dispatch mode is a KeyError, never a silent fallback."""
    with pytest.raises(KeyError):
        create_team_tools(role="teammate", agent_team=_backend(db, DEV_1, False), dispatch_mode="bogus")


# ---------------------------------------------------------------------------
# Variants keep their identity; only the schema/description change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("dispatch_mode", ["autonomous", "scheduled"])
async def test_variants_keep_card_identity(db, dispatch_mode):
    """Downstream (permission sets, MCP, logs) keys off name/id — they must not move."""
    leader_tools = create_team_tools(
        role="leader", agent_team=_backend(db, LEADER_NAME, True), dispatch_mode=dispatch_mode
    )
    create_task = _by_name(leader_tools, "create_task")
    assert create_task.card.id == "team.create_task"

    member_tools = create_team_tools(
        role="teammate", agent_team=_backend(db, DEV_1, False), dispatch_mode=dispatch_mode
    )
    send_message = _by_name(member_tools, "send_message")
    assert send_message.card.id == "team.send_message"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_variant_classes_and_schema(db):
    """Only the scheduled create_task exposes (and requires) ``assignee``."""
    backend = _backend(db, LEADER_NAME, True)
    autonomous = _by_name(create_team_tools(role="leader", agent_team=backend), "create_task")
    scheduled = _by_name(
        create_team_tools(role="leader", agent_team=backend, dispatch_mode="scheduled"), "create_task"
    )
    assert isinstance(autonomous, TaskCreateTool)
    assert isinstance(scheduled, ScheduledTaskCreateTool)

    def node(tool):
        return tool.card.input_params["properties"]["tasks"]["items"]

    assert "assignee" not in node(autonomous)["properties"]
    assert "assignee" in node(scheduled)["properties"]
    assert "assignee" not in node(autonomous)["required"]
    assert "assignee" in node(scheduled)["required"]

    # Parameter descriptions are shared: same locale key, same string.
    assert node(autonomous)["properties"]["title"]["description"] == node(scheduled)["properties"]["title"]["description"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_send_message_variant_narrows_to_enum(db):
    """Scheduled members see two recipients and no anyOf — schema is the contract."""
    leader_tools = create_team_tools(
        role="leader", agent_team=_backend(db, LEADER_NAME, True), dispatch_mode="scheduled"
    )
    leader_send = _by_name(leader_tools, "send_message")
    assert isinstance(leader_send, SendMessageTool)
    assert "anyOf" in leader_send.card.input_params["properties"]["to"]

    member_tools = create_team_tools(
        role="teammate", agent_team=_backend(db, DEV_1, False), dispatch_mode="scheduled"
    )
    member_send = _by_name(member_tools, "send_message")
    assert isinstance(member_send, ReportToLeaderTool)
    to_schema = member_send.card.input_params["properties"]["to"]
    assert "anyOf" not in to_schema
    # The enum is role words, not the concrete leader member_name.
    assert to_schema["enum"] == ["leader", "user"]
    assert LEADER_NAME not in to_schema["enum"]

    # content/summary descriptions are reused verbatim from send_message.*
    assert (
        member_send.card.input_params["properties"]["content"]["description"]
        == leader_send.card.input_params["properties"]["content"]["description"]
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_report_to_leader_resolves_leader_from_db(db):
    """A member never handed a leader name resolves it from the team_info row."""
    backend = TeamBackend(
        team_name=TEAM_NAME,
        member_name=DEV_1,
        is_leader=False,
        db=db,
        messager=AsyncMock(spec=Messager),
    )
    assert backend.leader_member_name == ""  # not seeded at construction
    assert await backend.resolve_leader_member_name() == LEADER_NAME  # read from DB

    tools = create_team_tools(role="teammate", agent_team=backend, dispatch_mode="scheduled")
    send = _by_name(tools, "send_message")
    result = await send.invoke({"to": "leader", "content": "done"})
    assert result.success
    assert result.data["to"] == LEADER_NAME


@pytest.mark.asyncio
@pytest.mark.level0
async def test_report_to_leader_soft_fails_when_leader_unresolvable(db):
    """No leader on record (no team row) -> to="leader" fails at delivery, not construction."""
    backend = TeamBackend(
        team_name="ghost_team_with_no_row",
        member_name=DEV_1,
        is_leader=False,
        db=db,
        messager=AsyncMock(spec=Messager),
    )
    # The role-word enum still assembles — resolution is deferred to delivery.
    tools = create_team_tools(role="teammate", agent_team=backend, dispatch_mode="scheduled")
    send = _by_name(tools, "send_message")
    assert send.card.input_params["properties"]["to"]["enum"] == ["leader", "user"]

    result = await send.invoke({"to": "leader", "content": "done"})
    assert not result.success
    assert "leader" in result.error


# ---------------------------------------------------------------------------
# Runtime behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_report_to_leader_rejects_peers_at_invoke(db):
    """MCP clients call invoke() without schema validation — invoke enforces too."""
    tools = create_team_tools(
        role="teammate", agent_team=_backend(db, DEV_1, False), dispatch_mode="scheduled"
    )
    send = _by_name(tools, "send_message")

    peer = await send.invoke({"to": DEV_2, "content": "psst"})
    assert not peer.success
    assert "must be one of" in peer.error

    # The concrete leader member_name is not a valid recipient — only the role
    # word is. This keeps the schema and the enforcement in lockstep.
    by_name = await send.invoke({"to": LEADER_NAME, "content": "done"})
    assert not by_name.success

    broadcast = await send.invoke({"to": "*", "content": "hi all"})
    assert not broadcast.success

    multicast = await send.invoke({"to": [DEV_2], "content": "hi"})
    assert not multicast.success
    assert "must be a string" in multicast.error

    # The role word resolves to the real leader and delivers.
    to_leader = await send.invoke({"to": "leader", "content": "done"})
    assert to_leader.success
    assert to_leader.data["to"] == LEADER_NAME

    to_user = await send.invoke({"to": "user", "content": "answering you"})
    assert to_user.success


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_create_task_rejects_unknown_or_missing_assignee(db):
    """Assignee validation happens at the tool boundary, before the transaction."""
    tools = create_team_tools(role="leader", agent_team=_backend(db, LEADER_NAME, True), dispatch_mode="scheduled")
    create_task = _by_name(tools, "create_task")

    missing = await create_task.invoke({"tasks": [{"title": "t", "content": "c"}]})
    assert not missing.success
    assert "assignee" in missing.error

    unknown = await create_task.invoke({"tasks": [{"title": "t", "content": "c", "assignee": "ghost"}]})
    assert not unknown.success
    assert "not found" in unknown.error


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_create_task_lands_assignee_atomically(db):
    """One atomic add_graph: assignee rides along; assignment != execution.

    Both tasks land with their owner on record but neither starts here —
    assignment and execution-start are separate events in scheduled dispatch.
    The unblocked task rests at PENDING(assignee) awaiting the scheduler; the
    dependent one is BLOCKED(assignee).
    """
    backend = _backend(db, LEADER_NAME, True)
    tools = create_team_tools(role="leader", agent_team=backend, dispatch_mode="scheduled")
    create_task = _by_name(tools, "create_task")

    result = await create_task.invoke(
        {
            "tasks": [
                {"task_id": "t1", "title": "first", "content": "c", "assignee": DEV_1},
                {"task_id": "t2", "title": "second", "content": "c", "assignee": DEV_2, "depends_on": ["t1"]},
            ]
        }
    )
    assert result.success, result.error

    t1 = await backend.task_manager.get("t1")
    t2 = await backend.task_manager.get("t2")
    # No dependencies -> assigned but not started; the scheduler starts it later.
    assert t1.status == TaskStatus.PENDING.value
    assert t1.assignee == DEV_1
    # Blocked, and the owner is already on record — no follow-up assign needed.
    assert t2.status == TaskStatus.BLOCKED.value
    assert t2.assignee == DEV_2

    text = create_task.map_result(result)
    assert DEV_1 in text and DEV_2 in text
    assert "blocked" in text


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_task_starts_and_completes(db):
    """The scheduled path: PENDING(assignee) -> IN_PROGRESS -> COMPLETED.

    ``start_task`` (called by the scheduler) is the only thing that moves a
    task off PENDING in scheduled dispatch; a member never claims.
    """
    backend = _backend(db, LEADER_NAME, True)
    tm = backend.task_manager
    tools = create_team_tools(role="leader", agent_team=backend, dispatch_mode="scheduled")
    create_task = _by_name(tools, "create_task")

    result = await create_task.invoke(
        {"tasks": [{"task_id": "s1", "title": "solo", "content": "c", "assignee": DEV_1}]}
    )
    assert result.success, result.error
    assert (await tm.get("s1")).status == TaskStatus.PENDING.value

    # The scheduler starts it -> IN_PROGRESS, owner unchanged.
    assert (await tm.start_task("s1")).ok
    started = await tm.get("s1")
    assert started.status == TaskStatus.IN_PROGRESS.value
    assert started.assignee == DEV_1

    # Idempotent re-start is a no-op success.
    assert (await tm.start_task("s1")).ok

    # A build-mode member completes straight from IN_PROGRESS.
    tm_dev = TeamTaskManager(team_name=TEAM_NAME, member_name=DEV_1, db=db, messager=AsyncMock(spec=Messager))
    assert (await tm_dev.complete("s1")).ok
    assert (await tm.get("s1")).status == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_scheduled_start_enforces_one_active_task(db):
    """A member may hold at most one active (PLANNING/IN_PROGRESS/IN_REVIEW) task at a time."""
    backend = _backend(db, LEADER_NAME, True)
    tm = backend.task_manager
    tools = create_team_tools(role="leader", agent_team=backend, dispatch_mode="scheduled")
    create_task = _by_name(tools, "create_task")

    await create_task.invoke(
        {
            "tasks": [
                {"task_id": "a", "title": "a", "content": "c", "assignee": DEV_1},
                {"task_id": "b", "title": "b", "content": "c", "assignee": DEV_1},
            ]
        }
    )
    assert (await tm.start_task("a")).ok
    # Starting a second task for the same member is rejected while "a" runs.
    second = await tm.start_task("b")
    assert not second.ok
    assert "active task" in second.reason
    assert (await tm.get("b")).status == TaskStatus.PENDING.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_task_rejects_unassigned(db):
    """An unassigned (autonomous) pending task cannot be started."""
    backend = _backend(db, LEADER_NAME, True)
    tm = backend.task_manager
    tools = create_team_tools(role="leader", agent_team=backend)
    create_task = _by_name(tools, "create_task")

    await create_task.invoke({"tasks": [{"task_id": "u1", "title": "x", "content": "c"}]})
    result = await tm.start_task("u1")
    assert not result.ok
    assert "no assignee" in result.reason


@pytest.mark.asyncio
@pytest.mark.level0
async def test_autonomous_create_task_leaves_tasks_unassigned(db):
    """The autonomous variant never writes an assignee — members claim instead."""
    backend = _backend(db, LEADER_NAME, True)
    tools = create_team_tools(role="leader", agent_team=backend)
    create_task = _by_name(tools, "create_task")

    result = await create_task.invoke({"tasks": [{"task_id": "a1", "title": "x", "content": "c"}]})
    assert result.success, result.error

    task = await backend.task_manager.get("a1")
    assert task.status == TaskStatus.PENDING.value
    assert task.assignee is None


# ---------------------------------------------------------------------------
# The one test that catches a missing en fragment / locale key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize("dispatch_mode", ["autonomous", "scheduled"])
@pytest.mark.parametrize("role", ["leader", "teammate", "human_agent"])
async def test_every_toolset_assembles(db, lang, dispatch_mode, role):
    """Cartesian smoke test.

    Covers, in one sweep: a missing ``_desc`` file, a missing shared fragment,
    a missing STRINGS parameter key, and a missing entry in a variant table —
    every one of them raises during ``create_team_tools``.
    """
    is_leader = role == "leader"
    member = LEADER_NAME if is_leader else DEV_1
    tools = create_team_tools(
        role=role,
        agent_team=_backend(db, member, is_leader),
        dispatch_mode=dispatch_mode,
        lang=lang,
    )
    assert tools
    for tool in tools:
        assert tool.card.description
        assert "{{" not in tool.card.description


# ---------------------------------------------------------------------------
# Verify gate (F_59): reviewer column + verify_task tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize(
    "role,dispatch_mode,has_verify",
    [
        ("teammate", "autonomous", True),
        ("teammate", "scheduled", True),
        ("human_agent", "autonomous", True),
        ("leader", "autonomous", False),
    ],
)
async def test_verify_task_registered_for_members_not_leader(db, role, dispatch_mode, has_verify):
    """verify_task is a member/reviewer capability; the leader assigns reviewers, not verifies."""
    is_leader = role == "leader"
    member = LEADER_NAME if is_leader else DEV_1
    tools = create_team_tools(
        role=role,
        agent_team=_backend(db, member, is_leader),
        dispatch_mode=dispatch_mode,
    )
    assert ("verify_task" in _tool_names(tools)) is has_verify


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_carries_reviewer(db):
    """A leader-created task persists its reviewer list."""
    backend = _backend(db, LEADER_NAME, True)
    tools = create_team_tools(role="leader", agent_team=backend)
    create_task = _by_name(tools, "create_task")

    result = await create_task.invoke(
        {"tasks": [{"task_id": "r1", "title": "t", "content": "c", "reviewer": [DEV_2]}]}
    )
    assert result.success, result.error
    task = await backend.task_manager.get("r1")
    assert task.reviewers() == [DEV_2]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_rejects_reviewer_equal_assignee(db):
    """A reviewer may not be the task's own author."""
    backend = _backend(db, LEADER_NAME, True)
    tools = create_team_tools(role="leader", agent_team=backend, dispatch_mode="scheduled")
    create_task = _by_name(tools, "create_task")

    result = await create_task.invoke(
        {"tasks": [{"task_id": "r1", "title": "t", "content": "c", "assignee": DEV_1, "reviewer": [DEV_1]}]}
    )
    assert not result.success
    assert "their own task" in result.error


@pytest.mark.asyncio
@pytest.mark.level0
async def test_create_task_rejects_unknown_reviewer(db):
    """A reviewer must be a real team member."""
    backend = _backend(db, LEADER_NAME, True)
    tools = create_team_tools(role="leader", agent_team=backend)
    create_task = _by_name(tools, "create_task")

    result = await create_task.invoke(
        {"tasks": [{"task_id": "r1", "title": "t", "content": "c", "reviewer": ["ghost"]}]}
    )
    assert not result.success
    assert "not found" in result.error


@pytest.mark.asyncio
@pytest.mark.level0
async def test_verify_task_tool_pass_flow(db):
    """VerifyTaskTool wires a reviewer's pass verdict through to COMPLETED."""
    # Leader assigns an author + reviewer, author completes -> IN_REVIEW.
    leader_tm = TeamTaskManager(team_name=TEAM_NAME, member_name=LEADER_NAME, db=db, messager=AsyncMock(spec=Messager))
    from openjiuwen.agent_teams.schema.task import TaskGraphSpec

    await leader_tm.add_graph(
        [TaskGraphSpec(title="w", content="c", task_id="v1", assignee=DEV_1, reviewer=(DEV_2,))]
    )
    await leader_tm.start_task("v1")
    author_tm = TeamTaskManager(team_name=TEAM_NAME, member_name=DEV_1, db=db, messager=AsyncMock(spec=Messager))
    await author_tm.complete("v1")
    assert (await db.task.get_task("v1")).status == TaskStatus.IN_REVIEW.value

    # Reviewer DEV_2 verifies via the tool.
    reviewer_tools = create_team_tools(role="teammate", agent_team=_backend(db, DEV_2, False))
    verify = _by_name(reviewer_tools, "verify_task")
    result = await verify.invoke({"task_id": "v1", "decision": "pass"})
    assert result.success, result.error
    assert (await db.task.get_task("v1")).status == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.level0
async def test_view_task_in_review_lists_reviewers_tasks(db):
    """view_task(action=in_review) surfaces the tasks a member must verify."""
    leader_tm = TeamTaskManager(team_name=TEAM_NAME, member_name=LEADER_NAME, db=db, messager=AsyncMock(spec=Messager))
    from openjiuwen.agent_teams.schema.task import TaskGraphSpec

    await leader_tm.add_graph(
        [TaskGraphSpec(title="w", content="c", task_id="v1", assignee=DEV_1, reviewer=(DEV_2,))]
    )
    await leader_tm.start_task("v1")
    author_tm = TeamTaskManager(team_name=TEAM_NAME, member_name=DEV_1, db=db, messager=AsyncMock(spec=Messager))
    await author_tm.complete("v1")

    reviewer_tools = create_team_tools(role="teammate", agent_team=_backend(db, DEV_2, False))
    view = _by_name(reviewer_tools, "view_task")
    assert "in_review" in view.card.input_params["properties"]["action"]["enum"]
    result = await view.invoke({"action": "in_review"})
    assert result.success
    assert [task["task_id"] for task in result.data["tasks"]] == ["v1"]
