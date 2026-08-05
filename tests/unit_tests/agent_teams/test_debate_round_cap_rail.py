# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.rails.debate_round_cap_rail import DebateRoundCapRail
from openjiuwen.harness.tools.base_tool import ToolOutput


def _ctx(*, tool_name: str = "send_message", tool_args=None, tool_result=None, skip: bool = False):
    inputs = SimpleNamespace(
        tool_name=tool_name,
        tool_args=tool_args if tool_args is not None else {"to": "discuss", "content": "hi"},
        tool_call=SimpleNamespace(id="tc-1"),
        tool_result=tool_result,
        tool_msg=None,
    )
    return SimpleNamespace(inputs=inputs, extra={"_skip_tool": True} if skip else {})


def _rail(*, max_rounds: int = 2, language: str = "cn") -> DebateRoundCapRail:
    backend = MagicMock()
    backend.leader_member_name = "team-leader"
    backend._leader_name_cache = None
    backend.task_manager = MagicMock()
    backend.task_manager.list_tasks = AsyncMock(return_value=[])
    backend.resolve_leader_member_name = AsyncMock(return_value="team-leader")

    messages = MagicMock()
    messages.send_message = AsyncMock(return_value="msg-1")

    return DebateRoundCapRail(
        max_debate_rounds=max_rounds,
        team_backend=backend,
        message_manager=messages,
        member_name="search",
        language=language,
    )


@pytest.mark.asyncio
async def test_counts_in_after_notifies_on_cap_then_before_rejects() -> None:
    rail = _rail(max_rounds=2)

    await rail.after_tool_call(_ctx(tool_result=ToolOutput(success=True, data={})))
    assert rail._count == 1
    rail._messages.send_message.assert_not_awaited()

    await rail.after_tool_call(_ctx(tool_result=ToolOutput(success=True, data={})))
    assert rail._count == 2
    rail._messages.send_message.assert_awaited_once()
    _args, kwargs = rail._messages.send_message.await_args
    assert kwargs.get("to_member_name") == "team-leader"

    ctx = _ctx()
    await rail.before_tool_call(ctx)
    assert ctx.extra.get("_skip_tool") is True
    assert "上限" in ctx.inputs.tool_msg.content
    assert rail._messages.send_message.await_count == 1


@pytest.mark.asyncio
async def test_does_not_count_leader_or_user_targets() -> None:
    rail = _rail(max_rounds=1)

    await rail.after_tool_call(
        _ctx(tool_args={"to": "team-leader", "content": "report"}, tool_result=ToolOutput(success=True, data={}))
    )
    await rail.after_tool_call(
        _ctx(tool_args={"to": "user", "content": "hi"}, tool_result=ToolOutput(success=True, data={}))
    )
    assert rail._count == 0

    ctx = _ctx(tool_args={"to": "team-leader", "content": "still ok"})
    await rail.before_tool_call(ctx)
    assert not ctx.extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_broadcast_to_all_counts_as_one() -> None:
    rail = _rail(max_rounds=1)
    await rail.after_tool_call(
        _ctx(tool_args={"to": "*", "content": "all"}, tool_result=ToolOutput(success=True, data={}))
    )
    assert rail._count == 1
    rail._messages.send_message.assert_awaited_once()

    ctx = _ctx(tool_args={"to": "*", "content": "again"})
    await rail.before_tool_call(ctx)
    assert ctx.extra.get("_skip_tool") is True


@pytest.mark.asyncio
async def test_multicast_counts_as_one() -> None:
    rail = _rail(max_rounds=1)
    await rail.after_tool_call(
        _ctx(
            tool_args={"to": ["discuss", "teacher"], "content": "ping"},
            tool_result=ToolOutput(success=True, data={}),
        )
    )
    assert rail._count == 1

    ctx = _ctx(tool_args={"to": ["discuss"], "content": "again"})
    await rail.before_tool_call(ctx)
    assert ctx.extra.get("_skip_tool") is True


@pytest.mark.asyncio
async def test_inactive_when_open_tasks_exist() -> None:
    rail = _rail(max_rounds=1)
    rail._team.task_manager.list_tasks = AsyncMock(return_value=[SimpleNamespace(status="in_progress")])

    await rail.after_tool_call(_ctx(tool_result=ToolOutput(success=True, data={})))
    assert rail._count == 0

    ctx = _ctx()
    await rail.before_tool_call(ctx)
    assert not ctx.extra.get("_skip_tool")


@pytest.mark.asyncio
async def test_factory_returns_none_for_leader() -> None:
    from openjiuwen.agent_teams.rails.elements import build_team_debate_round_cap_rail
    from openjiuwen.agent_teams.rails.team_context import inject_team_handles
    from openjiuwen.agent_teams.schema.build_context import BuildContext

    ctx = BuildContext(member_name="team-leader", role="leader", language="cn")
    inject_team_handles(
        ctx.extras,
        team_backend=MagicMock(team_name="t", db=MagicMock()),
        messager=MagicMock(),
    )
    result = build_team_debate_round_cap_rail(
        {"max_debate_rounds": 5, "team_name": "t"},
        ctx,
    )
    assert result is None


@pytest.mark.asyncio
async def test_factory_builds_for_teammate() -> None:
    from openjiuwen.agent_teams.rails.debate_round_cap_rail import DebateRoundCapRail
    from openjiuwen.agent_teams.rails.elements import build_team_debate_round_cap_rail
    from openjiuwen.agent_teams.rails.team_context import inject_team_handles
    from openjiuwen.agent_teams.schema.build_context import BuildContext

    backend = MagicMock(team_name="t", db=MagicMock())
    ctx = BuildContext(member_name="search", role="teammate", language="cn")
    inject_team_handles(ctx.extras, team_backend=backend, messager=MagicMock())
    result = build_team_debate_round_cap_rail(
        {"max_debate_rounds": 5, "team_name": "t"},
        ctx,
    )
    assert isinstance(result, DebateRoundCapRail)
    assert result._max == 5
