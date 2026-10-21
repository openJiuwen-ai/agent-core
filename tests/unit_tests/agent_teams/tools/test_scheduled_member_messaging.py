# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for scheduled-dispatch peer messaging (F_126).

``TeamAgentSpec.enable_member_messaging`` gives a scheduled member back the
full ``send_message`` reach. One backend gate drives three surfaces that must
agree: the member tool variant, the scheduled dispatch prompts, and the
leader's ``build_team`` disclosure. These tests pin all three, plus the
``"leader"`` role word that survives in the opened variant.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.descriptor import TeamJoinDescriptor
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.prompts.sections import (
    build_leader_policy_disclosure,
    build_team_dispatch_section,
)
from openjiuwen.agent_teams.schema.blueprint import LeaderSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.status import MemberMode
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.tools.tool_message import (
    ReportToLeaderTool,
    ScheduledPeerMessageTool,
    SendMessageTool,
)
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness.tools.base_tool import ToolOutput

TEAM_NAME = "peer_team"
LEADER_NAME = "team_leader"
DEV_1 = "dev-1"
DEV_2 = "dev-2"
DEV_3 = "dev-3"


@pytest_asyncio.fixture
async def db():
    """In-memory team DB with a leader and three teammates."""
    token = set_session_id("peer_session")
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    database = TeamDatabase(config)
    try:
        await database.initialize()
        await database.team.create_team(
            team_name=TEAM_NAME,
            display_name="Peer Team",
            leader_member_name=LEADER_NAME,
        )
        for name in (LEADER_NAME, DEV_1, DEV_2, DEV_3):
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


def _backend(
    db,
    member_name: str,
    *,
    is_leader: bool = False,
    dispatch_mode: str = "scheduled",
    member_messaging: bool = True,
) -> TeamBackend:
    return TeamBackend(
        team_name=TEAM_NAME,
        member_name=member_name,
        is_leader=is_leader,
        db=db,
        messager=AsyncMock(spec=Messager),
        dispatch_mode=dispatch_mode,
        enable_member_messaging=member_messaging,
    )


def _send_tool(
    db,
    member_name: str = DEV_1,
    *,
    is_leader: bool = False,
    dispatch_mode: str = "scheduled",
    member_messaging: bool = True,
    lang: str = "cn",
):
    backend = _backend(
        db,
        member_name,
        is_leader=is_leader,
        dispatch_mode=dispatch_mode,
        member_messaging=member_messaging,
    )
    tools = create_team_tools(
        role="leader" if is_leader else "teammate",
        agent_team=backend,
        dispatch_mode=dispatch_mode,
        lang=lang,
    )
    return next(tool for tool in tools if tool.card.name == "send_message")


async def _inbox(db, member_name: str):
    return await db.message.get_messages(TEAM_NAME, to_member_name=member_name)


# ---------------------------------------------------------------------------
# Variant selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_flag_off_keeps_report_to_leader(db):
    """The default stays narrowed: leader and user only."""
    send = _send_tool(db, member_messaging=False)
    assert isinstance(send, ReportToLeaderTool)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_flag_on_opens_full_reach_for_scheduled_member(db):
    """A scheduled member gets unicast + multicast + broadcast back."""
    send = _send_tool(db)
    assert isinstance(send, ScheduledPeerMessageTool)
    properties = send.card.input_params["properties"]
    assert "enum" not in properties["to"]
    assert properties["targets"]["type"] == "array"
    assert send.card.input_params["required"] == ["content"]
    assert "leader" in properties["to"]["description"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_flag_never_changes_leader_or_autonomous_member(db):
    """The leader already has full reach, and autonomous members reach peers anyway."""
    leader_send = _send_tool(db, LEADER_NAME, is_leader=True)
    assert type(leader_send) is SendMessageTool
    autonomous_send = _send_tool(db, dispatch_mode="autonomous")
    assert type(autonomous_send) is SendMessageTool


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
async def test_opened_variant_description_renders(db, lang):
    """The opened variant has its own description, fragment filled, in both languages."""
    send = _send_tool(db, lang=lang)
    assert isinstance(send, ScheduledPeerMessageTool)
    assert "{{" not in send.card.description
    assert "summary" in send.card.description


# ---------------------------------------------------------------------------
# Delivery through the opened variant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_messages_a_peer_directly(db):
    send = _send_tool(db)
    result = await send.invoke({"to": DEV_2, "content": "接口字段改名为 user_id"})
    assert result.success
    assert result.data["to"] == DEV_2
    assert len(await _inbox(db, DEV_2)) == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_role_word_resolves_to_real_leader(db):
    send = _send_tool(db)
    result = await send.invoke({"to": "leader", "content": "上游产物缺少字段"})
    assert result.success
    assert result.data["to"] == LEADER_NAME


@pytest.mark.asyncio
@pytest.mark.level0
async def test_multicast_resolves_leader_role_word(db):
    send = _send_tool(db)
    result = await send.invoke({"targets": ["leader", DEV_2], "content": "接口已冻结"})
    assert result.success
    assert result.data["delivered"] == [LEADER_NAME, DEV_2]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_can_broadcast(db):
    send = _send_tool(db)
    result = await send.invoke({"to": "*", "content": "共享目录已切换"})
    assert result.success
    assert result.data["type"] == "broadcast"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_unknown_peer_is_rejected(db):
    send = _send_tool(db)
    result = await send.invoke({"to": "ghost", "content": "hi"})
    assert not result.success
    assert "ghost" in result.error


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_role_word_soft_fails_without_leader_on_record(db):
    backend = TeamBackend(
        team_name="ghost_team_with_no_row",
        member_name=DEV_1,
        is_leader=False,
        db=db,
        messager=AsyncMock(spec=Messager),
        dispatch_mode="scheduled",
        enable_member_messaging=True,
    )
    tools = create_team_tools(role="teammate", agent_team=backend, dispatch_mode="scheduled")
    send = next(tool for tool in tools if tool.card.name == "send_message")
    for inputs in ({"to": "leader", "content": "x"}, {"targets": ["leader", DEV_2], "content": "x"}):
        result = await send.invoke(inputs)
        assert not result.success
        assert "leader" in result.error


# ---------------------------------------------------------------------------
# Prompts follow the same gate
# ---------------------------------------------------------------------------


def _dispatch_text(role: TeamRole, lang: str, member_messaging: bool) -> str:
    section = build_team_dispatch_section(
        role=role,
        dispatch_mode="scheduled",
        member_messaging=member_messaging,
        language=lang,
    )
    return section.content[lang]


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
def test_leader_dispatch_prompt_switches_messaging_rule(lang):
    closed = _dispatch_text(TeamRole.LEADER, lang, False)
    opened = _dispatch_text(TeamRole.LEADER, lang, True)
    closed_marker = "成员之间不能直接通信" if lang == "cn" else "Members cannot talk to each other"
    open_marker = "成员之间可以直接通信" if lang == "cn" else "Members can talk to each other"
    assert closed_marker in closed and open_marker not in closed
    assert open_marker in opened and closed_marker not in opened
    for text in (closed, opened):
        assert "{{" not in text


@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
def test_teammate_dispatch_prompt_only_grows_when_opened(lang):
    closed = _dispatch_text(TeamRole.TEAMMATE, lang, False)
    opened = _dispatch_text(TeamRole.TEAMMATE, lang, True)
    open_marker = "可以直接联系其他成员" if lang == "cn" else "You may contact other members directly"
    assert open_marker not in closed
    assert open_marker in opened
    assert "{{" not in closed
    # The empty slot sits on the last line, so nothing is left dangling.
    assert closed == closed.rstrip("\n") + "\n"
    assert not closed.rstrip("\n").endswith("\n")


@pytest.mark.level0
def test_autonomous_dispatch_prompt_ignores_the_flag():
    for role in (TeamRole.LEADER, TeamRole.TEAMMATE):
        off = build_team_dispatch_section(role=role, dispatch_mode="autonomous", member_messaging=False)
        on = build_team_dispatch_section(role=role, dispatch_mode="autonomous", member_messaging=True)
        assert off.content == on.content


@pytest.mark.level0
def test_leader_disclosure_carries_the_open_rule():
    text = build_leader_policy_disclosure(dispatch_mode="scheduled", member_messaging=True)
    assert "成员之间可以直接通信" in text
    assert "成员之间不能直接通信" not in text


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_team_disclosure_reads_backend_gate(db):
    """The build_team result discloses the rule the members' tools actually follow."""
    backend = _backend(db, LEADER_NAME, is_leader=True)
    tools = create_team_tools(role="leader", agent_team=backend, dispatch_mode="scheduled")
    build_team = next(tool for tool in tools if tool.card.name == "build_team")
    rendered = build_team.render_for_llm(
        ToolOutput(success=True, data={"team_name": TEAM_NAME, "enable_hitt": False}),
    )
    assert "成员之间可以直接通信" in rendered


# ---------------------------------------------------------------------------
# Spec plumbing
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_spec_flag_reaches_runtime_team_spec():
    spec = TeamAgentSpec(
        team_name="t",
        leader=LeaderSpec(member_name=LEADER_NAME),
        agents={},
        dispatch_mode="scheduled",
        enable_member_messaging=True,
    )
    assert spec.enable_member_messaging is True
    assert TeamAgentSpec(team_name="t", agents={}).enable_member_messaging is False


@pytest.mark.level0
def test_descriptor_carries_the_flag_through_json():
    descriptor = TeamJoinDescriptor(
        session_id="s",
        team_name=TEAM_NAME,
        member_name=DEV_1,
        scope="member",
        dispatch_mode="scheduled",
        enable_member_messaging=True,
    )
    restored = TeamJoinDescriptor.model_validate_json(descriptor.to_json())
    assert restored.enable_member_messaging is True
