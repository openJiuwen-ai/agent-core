# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the team-member MCP server, scoped by descriptor.scope.

``member`` scope exposes the real teammate ``TeamTool`` set (view_task /
claim_task / send_message) + the external-only read_inbox, with empty
server-level instructions. ``operator`` scope exposes the broad external
team-control set + workflow instructions. The server is the low-level
:class:`mcp.server.lowlevel.Server`, so tools are driven through its
registered request handlers.
"""

import mcp.types as types
import pytest

from openjiuwen.agent_teams.external import ExternalTeamClient
from openjiuwen.agent_teams.mcp.server import build_server
from openjiuwen.agent_teams.schema.status import TaskStatus

_MEMBER_TOOLS = {"read_inbox", "view_task", "claim_task", "send_message"}
_OPERATOR_TOOLS = {
    "read_inbox",
    "send_message",
    "create_task",
    "update_task",
    "list_tasks",
    "claimable_tasks",
    "get_task",
    "claim_task",
    "complete_task",
    "list_members",
}


def _factory(make_descriptor, *, member="dev-1", role="teammate", scope="member"):
    async def _connect() -> ExternalTeamClient:
        client = ExternalTeamClient(make_descriptor(member=member, role=role, scope=scope))
        await client.connect()
        return client

    return _connect


async def _list_names(server) -> set[str]:
    handler = server.request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list"))
    return {tool.name for tool in result.root.tools}


async def _call_text(server, name: str, arguments: dict) -> str:
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(req)
    return result.root.content[0].text


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_scope_exposes_real_teammate_tools(team_db, make_descriptor):
    server = build_server(_factory(make_descriptor, scope="member"), scope="member")
    assert await _list_names(server) == _MEMBER_TOOLS
    # Member instructions are empty — the team system prompt is injected into
    # the CLI directly at spawn time, so the protocol is not repeated here.
    assert not server.instructions


@pytest.mark.asyncio
@pytest.mark.level0
async def test_operator_scope_exposes_full_control_set(team_db, make_descriptor):
    server = build_server(_factory(make_descriptor, scope="operator"), scope="operator")
    assert await _list_names(server) == _OPERATOR_TOOLS
    assert server.instructions  # operator carries the control workflow


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_leader_role_still_member_toolset(team_db, make_descriptor):
    # scope is orthogonal to team role: a leader-role member descriptor still
    # gets the member tool set (operator is a separate, non-member scope).
    server = build_server(_factory(make_descriptor, member="leader", role="leader", scope="member"), scope="member")
    assert await _list_names(server) == _MEMBER_TOOLS


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_claim_then_complete_via_real_tool(team_db, make_descriptor):
    await team_db.task.create_task(
        task_id="t1",
        team_name="ext_team",
        title="Do X",
        content="details",
        status=TaskStatus.PENDING.value,
    )
    server = build_server(_factory(make_descriptor, scope="member"), scope="member")

    # claim_task carries the status — completion is the same tool, not a
    # separate complete_task (native-teammate parity).
    await _call_text(server, "claim_task", {"task_id": "t1", "status": "claimed"})
    task = await team_db.task.get_task("t1")
    assert task.assignee == "dev-1"
    assert task.status == TaskStatus.IN_PROGRESS.value

    text = await _call_text(server, "claim_task", {"task_id": "t1", "status": "completed"})
    task = await team_db.task.get_task("t1")
    assert task.status == TaskStatus.COMPLETED.value
    assert "t1" in text  # map_result text mentions the task


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_send_message_delivers(team_db, make_descriptor):
    server = build_server(_factory(make_descriptor, scope="member"), scope="member")
    await _call_text(server, "send_message", {"to": "leader", "content": "hi via mcp"})

    async with ExternalTeamClient(make_descriptor(member="leader", role="leader")) as leader:
        inbox = await leader.fetch_inbox()
        assert any(m.content == "hi via mcp" for m in inbox.messages)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_member_read_inbox_returns_text(team_db, make_descriptor):
    async with ExternalTeamClient(make_descriptor(member="leader", role="leader")) as leader:
        await leader.send_message("dev-1", "ping from leader")

    server = build_server(_factory(make_descriptor, scope="member"), scope="member")
    text = await _call_text(server, "read_inbox", {})
    assert "ping from leader" in text


@pytest.mark.asyncio
@pytest.mark.level1
async def test_operator_create_task_effect(team_db, make_descriptor):
    server = build_server(_factory(make_descriptor, member="op", role="leader", scope="operator"), scope="operator")
    await _call_text(server, "create_task", {"title": "Ship it", "content": "do the thing"})

    tasks = await team_db.task.get_team_tasks(team_name="ext_team")
    assert any(t.title == "Ship it" for t in tasks)
