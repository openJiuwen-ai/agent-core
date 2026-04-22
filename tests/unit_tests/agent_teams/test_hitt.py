# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for HITT (Human in the Team) feature.

Covers:
- Reserved name enforcement and auto-injection by ``TeamAgentSpec``.
- ``TeamBackend.build_team(enable_hitt=True)`` registering the
  human_agent member as READY.
- Human agent tool permission filtering.
- Task lock (``UpdateTaskTool``) honoring the human_agent claim.
- Message manager auto-marking messages to/for human_agent as read.
- ``interaction`` module routing (parse_mention, UserInbox,
  HumanAgentInbox).
- ``TeamRail.build_team_hitt_section`` role-specific content.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.agent.team_rail import build_team_hitt_section
from openjiuwen.agent_teams.constants import (
    HUMAN_AGENT_MEMBER_NAME,
    RESERVED_MEMBER_NAMES,
    USER_PSEUDO_MEMBER_NAME,
)
from openjiuwen.agent_teams.interaction import (
    HumanAgentInbox,
    HumanAgentNotEnabledError,
    UserInbox,
    is_reserved_name,
    parse_mention,
)
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.blueprint import (
    LeaderSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
    TaskStatus,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.agent_teams.spawn.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.team_tools import (
    HUMAN_AGENT_TOOLS,
    create_team_tools,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("hitt_session")
    database = TeamDatabase(db_config)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        reset_session_id(token)


@pytest_asyncio.fixture
async def messager():
    yield AsyncMock(spec=Messager)


@pytest_asyncio.fixture
async def team_backend(db, messager):
    backend = TeamBackend(
        team_name="hitt_team",
        member_name="team_leader",
        is_leader=True,
        db=db,
        messager=messager,
    )
    yield backend


# ---------------------------------------------------------------------------
# Router / reserved names
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_parse_mention_returns_target_and_body():
    assert parse_mention("@dev-1 please start task 123") == (
        "dev-1",
        "please start task 123",
    )


@pytest.mark.level0
def test_parse_mention_none_when_no_prefix():
    assert parse_mention("just a regular message") is None


@pytest.mark.level0
def test_parse_mention_none_when_empty():
    assert parse_mention("") is None


@pytest.mark.level0
def test_parse_mention_none_when_only_mention():
    # "@dev-1" without body → no body group, regex miss
    assert parse_mention("@dev-1") is None


@pytest.mark.level0
def test_parse_mention_allows_reserved_target():
    # Reserved names are valid mention targets: user may @leader / @human_agent
    parsed = parse_mention("@human_agent you decide")
    assert parsed == ("human_agent", "you decide")


@pytest.mark.level0
def test_is_reserved_name_enforced():
    for name in ("user", "team_leader", "human_agent"):
        assert is_reserved_name(name) is True
    assert is_reserved_name("backend-dev-1") is False


# ---------------------------------------------------------------------------
# TeamAgentSpec — auto-injection + validation
# ---------------------------------------------------------------------------


def _minimal_spec(**overrides) -> TeamAgentSpec:
    agents = {"leader": DeepAgentSpec()}
    base: dict = {"agents": agents, "team_name": "hitt_team"}
    base.update(overrides)
    return TeamAgentSpec(**base)


@pytest.mark.level0
def test_enable_hitt_injects_human_agent_member():
    spec = _minimal_spec(enable_hitt=True)
    spec._validate_reserved_names()
    spec._inject_human_agent_if_enabled()
    names = [m.member_name for m in spec.predefined_members]
    roles = [m.role_type for m in spec.predefined_members]
    assert HUMAN_AGENT_MEMBER_NAME in names
    assert TeamRole.HUMAN_AGENT in roles


@pytest.mark.level0
def test_enable_hitt_is_idempotent_on_existing_human_agent():
    pre = TeamMemberSpec(
        member_name=HUMAN_AGENT_MEMBER_NAME,
        display_name="Custom Human",
        role_type=TeamRole.HUMAN_AGENT,
        persona="Custom persona",
    )
    spec = _minimal_spec(enable_hitt=True, predefined_members=[pre])
    spec._inject_human_agent_if_enabled()
    human_slots = [m for m in spec.predefined_members if m.member_name == HUMAN_AGENT_MEMBER_NAME]
    assert len(human_slots) == 1
    # Pre-existing spec wins — no clobber.
    assert human_slots[0].persona == "Custom persona"


@pytest.mark.level0
def test_enable_hitt_false_skips_injection():
    spec = _minimal_spec(enable_hitt=False)
    spec._inject_human_agent_if_enabled()
    assert all(m.member_name != HUMAN_AGENT_MEMBER_NAME for m in spec.predefined_members)


@pytest.mark.level0
def test_leader_member_name_cannot_be_reserved():
    spec = _minimal_spec(leader=LeaderSpec(member_name=HUMAN_AGENT_MEMBER_NAME))
    with pytest.raises(ValueError, match="reserved"):
        spec._validate_reserved_names()


@pytest.mark.level0
def test_predefined_member_cannot_use_reserved_name():
    pre = TeamMemberSpec(
        member_name=USER_PSEUDO_MEMBER_NAME,
        display_name="x",
        persona="x",
    )
    spec = _minimal_spec(predefined_members=[pre])
    with pytest.raises(ValueError, match="reserved name"):
        spec._validate_reserved_names()


# ---------------------------------------------------------------------------
# TeamBackend — human_agent registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_team_enable_hitt_registers_human_agent(team_backend, db):
    await team_backend.build_team(
        display_name="HITT Team",
        desc="test",
        leader_display_name="Leader",
        leader_desc="Leader persona",
        enable_hitt=True,
    )
    member = await db.get_member(HUMAN_AGENT_MEMBER_NAME, "hitt_team")
    assert member is not None
    assert member.status == MemberStatus.READY.value
    assert member.execution_status == ExecutionStatus.IDLE.value
    assert team_backend.hitt_enabled() is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_team_without_hitt_skips_human_agent(team_backend, db):
    await team_backend.build_team(
        display_name="Plain Team",
        desc="test",
        leader_display_name="Leader",
        leader_desc="Leader persona",
        enable_hitt=False,
    )
    member = await db.get_member(HUMAN_AGENT_MEMBER_NAME, "hitt_team")
    assert member is None
    assert team_backend.hitt_enabled() is False


# ---------------------------------------------------------------------------
# Tool permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_role_only_gets_send_message(team_backend):
    tools = create_team_tools(role="human_agent", agent_team=team_backend)
    names = sorted(tool.card.name for tool in tools if tool.card is not None)
    assert names == sorted(HUMAN_AGENT_TOOLS)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_role_tools_exclude_human_agent_only(team_backend):
    tools = create_team_tools(role="leader", agent_team=team_backend)
    names = {tool.card.name for tool in tools if tool.card is not None}
    # Leader must retain build_team/update_task/create_task/send_message etc.
    assert {"build_team", "update_task", "send_message"}.issubset(names)


# ---------------------------------------------------------------------------
# Task lock (UpdateTaskTool) — leader cannot cancel/reassign human_agent tasks
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def built_team(team_backend, db):
    await team_backend.build_team(
        display_name="HITT Team",
        desc="t",
        leader_display_name="Leader",
        leader_desc="persona",
        enable_hitt=True,
    )
    yield team_backend


async def _create_and_assign(backend, db, task_id: str, assignee: str) -> None:
    create_result = await backend.task_manager.add(
        title="t",
        content="c",
        task_id=task_id,
    )
    assert create_result.ok, create_result.reason
    result = await backend.task_manager.assign(task_id, assignee)
    assert result.ok, result.reason


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cancel_task_owned_by_human_agent_is_refused(built_team, db):
    from openjiuwen.agent_teams.tools.locales import make_translator
    from openjiuwen.agent_teams.tools.team_tools import UpdateTaskTool

    await _create_and_assign(built_team, db, "t-1", HUMAN_AGENT_MEMBER_NAME)
    tool = UpdateTaskTool(built_team, make_translator("cn"))
    out = await tool.invoke({"task_id": "t-1", "status": "cancelled"})
    assert out.success is False
    assert "human_agent" in out.error
    # Task itself must still be claimed by human_agent.
    task = await built_team.task_manager.get("t-1")
    assert task.status == TaskStatus.CLAIMED.value
    assert task.assignee == HUMAN_AGENT_MEMBER_NAME


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reassign_task_owned_by_human_agent_is_refused(built_team, db):
    from openjiuwen.agent_teams.tools.locales import make_translator
    from openjiuwen.agent_teams.tools.team_tools import UpdateTaskTool

    await _create_and_assign(built_team, db, "t-2", HUMAN_AGENT_MEMBER_NAME)
    tool = UpdateTaskTool(built_team, make_translator("cn"))
    out = await tool.invoke({"task_id": "t-2", "assignee": "other-member"})
    assert out.success is False
    assert "human_agent" in out.error
    task = await built_team.task_manager.get("t-2")
    assert task.assignee == HUMAN_AGENT_MEMBER_NAME


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cancel_all_preserves_human_agent_claimed_task(built_team, db):
    # One human-claimed + one unassigned: cancel_all must keep the former.
    await _create_and_assign(built_team, db, "t-human", HUMAN_AGENT_MEMBER_NAME)
    await built_team.task_manager.add(
        title="open",
        content="c",
        task_id="t-open",
    )

    from openjiuwen.agent_teams.tools.locales import make_translator
    from openjiuwen.agent_teams.tools.team_tools import UpdateTaskTool

    tool = UpdateTaskTool(built_team, make_translator("cn"))
    out = await tool.invoke({"task_id": "*", "status": "cancelled"})
    assert out.success is True

    preserved = await built_team.task_manager.get("t-human")
    released = await built_team.task_manager.get("t-open")
    assert preserved.status == TaskStatus.CLAIMED.value
    assert released.status == TaskStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# Message auto-read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_direct_message_to_human_agent_is_auto_read(built_team, db):
    mm = built_team.message_manager
    msg_id = await mm.send_message(
        content="please review",
        to_member_name=HUMAN_AGENT_MEMBER_NAME,
    )
    assert msg_id is not None
    messages = await mm.get_messages(to_member_name=HUMAN_AGENT_MEMBER_NAME)
    assert len(messages) == 1
    assert messages[0].is_read is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_direct_message_to_regular_member_is_unread(team_backend, db):
    # Register a non-human member first.
    await team_backend.build_team(
        display_name="plain",
        desc="t",
        leader_display_name="Leader",
        leader_desc="p",
        enable_hitt=False,
    )
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    await team_backend.spawn_member(
        member_name="dev-1",
        display_name="Dev",
        agent_card=AgentCard(name="Dev"),
    )
    msg_id = await team_backend.message_manager.send_message(
        content="hi",
        to_member_name="dev-1",
    )
    assert msg_id is not None
    messages = await team_backend.message_manager.get_messages(to_member_name="dev-1")
    assert len(messages) == 1
    assert messages[0].is_read is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_broadcast_auto_advances_human_agent_read_watermark(built_team, db):
    mm = built_team.message_manager
    msg_id = await mm.broadcast_message(content="global announcement")
    assert msg_id is not None
    # human_agent's read_at must be set — unread-only fetch returns empty.
    unread = await mm.get_broadcast_messages(
        member_name=HUMAN_AGENT_MEMBER_NAME,
        unread_only=True,
    )
    assert unread == []


# ---------------------------------------------------------------------------
# Interaction inboxes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_user_inbox_direct_writes_as_user(team_backend, db):
    await team_backend.build_team(
        display_name="t",
        desc="t",
        leader_display_name="Leader",
        leader_desc="p",
    )
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard

    await team_backend.spawn_member(
        member_name="alice",
        display_name="Alice",
        agent_card=AgentCard(name="Alice"),
    )
    inbox = UserInbox(team_backend.message_manager)
    msg_id = await inbox.direct("alice", "look at this")
    assert msg_id is not None
    stored = await team_backend.message_manager.get_messages(to_member_name="alice")
    assert len(stored) == 1
    assert stored[0].from_member_name == USER_PSEUDO_MEMBER_NAME


@pytest.mark.asyncio
@pytest.mark.level0
async def test_user_inbox_broadcast_writes_as_user(team_backend, db):
    await team_backend.build_team(
        display_name="t",
        desc="t",
        leader_display_name="Leader",
        leader_desc="p",
    )
    inbox = UserInbox(team_backend.message_manager)
    msg_id = await inbox.broadcast("everyone read this")
    assert msg_id is not None
    broadcasts = await team_backend.message_manager.get_broadcast_messages(member_name="team_leader")
    assert any(m.from_member_name == USER_PSEUDO_MEMBER_NAME for m in broadcasts)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_inbox_raises_when_hitt_off(team_backend, db):
    await team_backend.build_team(
        display_name="t",
        desc="t",
        leader_display_name="Leader",
        leader_desc="p",
        enable_hitt=False,
    )
    inbox = HumanAgentInbox(team_backend, team_backend.message_manager)
    with pytest.raises(HumanAgentNotEnabledError):
        await inbox.send("hi")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_human_agent_inbox_sends_as_human_agent(built_team, db):
    inbox = HumanAgentInbox(built_team, built_team.message_manager)
    msg_id = await inbox.send("on it", to="team_leader")
    assert msg_id is not None
    stored = await built_team.message_manager.get_messages(to_member_name="team_leader")
    assert any(m.from_member_name == HUMAN_AGENT_MEMBER_NAME for m in stored)


# ---------------------------------------------------------------------------
# Rail HITT section
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_hitt_section_none_when_disabled():
    assert (
        build_team_hitt_section(
            role=TeamRole.LEADER,
            hitt_enabled=False,
            language="cn",
        )
        is None
    )


@pytest.mark.level0
def test_hitt_section_leader_mentions_lock_rules():
    section = build_team_hitt_section(
        role=TeamRole.LEADER,
        hitt_enabled=True,
        language="cn",
    )
    assert section is not None
    body = section.content["cn"]
    assert "human_agent" in body
    # Must spell out the ban on plain-text + the cancel/reassign lock.
    assert "send_message" in body
    assert "不能" in body or "禁止" in body


@pytest.mark.level0
def test_hitt_section_human_agent_describes_constrained_tools():
    section = build_team_hitt_section(
        role=TeamRole.HUMAN_AGENT,
        hitt_enabled=True,
        language="en",
    )
    assert section is not None
    body = section.content["en"]
    assert "send_message" in body
    assert "claim_task" in body or "do not" in body.lower()


# ---------------------------------------------------------------------------
# Reserved-name exports sanity
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_reserved_member_names_set_content():
    assert HUMAN_AGENT_MEMBER_NAME in RESERVED_MEMBER_NAMES
    assert USER_PSEUDO_MEMBER_NAME in RESERVED_MEMBER_NAMES
    assert "team_leader" in RESERVED_MEMBER_NAMES
