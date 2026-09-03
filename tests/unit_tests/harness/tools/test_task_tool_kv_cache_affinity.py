# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskTool coverage for Session-owned KVC lifecycle semantics."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.kv_cache import kv_cache_subagent_lifecycle
from openjiuwen.harness.tools.subagent.task_tool import TaskTool


class _FakeSubAgent:
    def __init__(self, agent_id: str, *, output: str = "done", error: Exception | None = None) -> None:
        self.card = AgentCard(id=agent_id, name=agent_id, description=agent_id)
        self.output = output
        self.error = error
        self.inputs: list[dict] = []
        self.sessions: list[Session | None] = []

    async def invoke(self, inputs: dict, session: Session | None = None) -> dict:
        self.inputs.append(dict(inputs))
        self.sessions.append(session)
        if self.error:
            raise self.error
        return {"output": self.output}


def _make_tool(*, enabled: bool = True, subagent: _FakeSubAgent) -> TaskTool:
    parent = SimpleNamespace(
        deep_config=SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled),
        ),
        create_subagent=lambda *_args, **_kwargs: subagent,
    )
    return TaskTool(ToolCard(id="task_tool", name="task_tool", description="task"), parent)


def test_team_member_subagent_scope_is_stable_and_distinct() -> None:
    kwargs = {
        "sub_session_id": "product-session_sub_browser_agent",
        "runtime_parent_session_id": "product-session",
    }
    member_a = kv_cache_subagent_lifecycle.scope_sub_session_id(
        **kwargs,
        parent_cache_id="team:product-session:team:team-a:member:member-a",
    )
    member_b = kv_cache_subagent_lifecycle.scope_sub_session_id(
        **kwargs,
        parent_cache_id="team:product-session:team:team-a:member:member-b",
    )

    assert member_a == kv_cache_subagent_lifecycle.scope_sub_session_id(
        **kwargs,
        parent_cache_id="team:product-session:team:team-a:member:member-a",
    )
    assert member_a != member_b


@pytest.mark.asyncio
async def test_sticky_subagent_uses_child_session_prepare_and_suspend() -> None:
    subagent = _FakeSubAgent("verification")
    tool = _make_tool(subagent=subagent)

    with patch.object(Session, "prepare_kvc", new=AsyncMock(return_value=True)) as prepare, patch.object(
        Session, "suspend_kvc", new=AsyncMock(return_value=True)
    ) as suspend, patch.object(Session, "release_kvc", new=AsyncMock(return_value=True)) as release:
        result = await tool.invoke(
            {"subagent_type": "verification_agent", "task_description": "run task"},
            session=Session(session_id="parent_session"),
        )

    assert result.success is True
    child = subagent.sessions[0]
    assert isinstance(child, Session)
    assert child.get_session_id() == "parent_session_sub_verification_agent"
    assert child.get_parent_session_id() == "parent_session"
    prepare.assert_awaited_once_with()
    suspend.assert_awaited_once_with()
    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_team_member_child_session_uses_member_cache_lineage() -> None:
    subagent = _FakeSubAgent("verification")
    tool = _make_tool(subagent=subagent)
    parent = Session(session_id="product-session")
    parent.set_team_cache_scope(team_id="team-a", agent_id="member-a")

    result = await tool.invoke(
        {"subagent_type": "verification_agent", "task_description": "run task"},
        session=parent,
    )

    assert result.success is True
    child = subagent.sessions[0]
    assert re.fullmatch(
        r"product-session_sub_verification_agent_scope_[0-9a-f]{12}",
        child.get_session_id(),
    )
    assert child.get_parent_session_id() == "team:product-session:team:team-a:member:member-a"


@pytest.mark.asyncio
async def test_sticky_failure_releases_child_and_preserves_original_exception() -> None:
    subagent = _FakeSubAgent("verification", error=RuntimeError("subagent boom"))
    tool = _make_tool(subagent=subagent)

    with patch.object(Session, "prepare_kvc", new=AsyncMock(return_value=True)) as prepare, patch.object(
        Session, "suspend_kvc", new=AsyncMock(return_value=True)
    ) as suspend, patch.object(Session, "release_kvc", new=AsyncMock(return_value=True)) as release:
        with pytest.raises(Exception, match="subagent boom"):
                await tool.invoke(
                    {"subagent_type": "verification_agent", "task_description": "run task"},
                    session=Session(session_id="parent_session"),
                )

    prepare.assert_awaited_once_with()
    suspend.assert_not_awaited()
    release.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_ephemeral_subagent_releases_without_prefetch() -> None:
    subagent = _FakeSubAgent("code")
    tool = _make_tool(subagent=subagent)

    with patch.object(Session, "prepare_kvc", new=AsyncMock(return_value=True)) as prepare, patch.object(
        Session, "release_kvc", new=AsyncMock(return_value=True)
    ) as release:
        result = await tool.invoke(
            {"subagent_type": "code", "task_description": "run task"},
            session=Session(session_id="parent_session"),
        )

    assert result.success is True
    assert re.fullmatch(r"parent_session_sub_code_[0-9a-f]{8}", subagent.sessions[0].get_session_id())
    prepare.assert_not_awaited()
    release.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_runtime_failures_do_not_override_success_result() -> None:
    runtime = SimpleNamespace(
        prepare=AsyncMock(side_effect=RuntimeError("prepare failed")),
        suspend=AsyncMock(side_effect=RuntimeError("suspend failed")),
        release=AsyncMock(side_effect=RuntimeError("release failed")),
    )
    subagent = _FakeSubAgent("browser", output="kept")
    tool = _make_tool(subagent=subagent)

    result = await tool.invoke(
        {"subagent_type": "browser_agent", "task_description": "run task"},
        session=Session(session_id="parent_session", kv_cache_runtime=runtime),
    )

    assert result.success is True
    assert result.data["output"] == "kept"


@pytest.mark.asyncio
async def test_affinity_disabled_preserves_baseline_invoke() -> None:
    subagent = _FakeSubAgent("browser")
    subagent.invoke = AsyncMock(wraps=subagent.invoke)
    tool = _make_tool(enabled=False, subagent=subagent)

    result = await tool.invoke(
        {"subagent_type": "browser_agent", "task_description": "run task"},
        session=Session(session_id="parent_session"),
    )

    assert result.success is True
    assert "session" not in subagent.invoke.await_args.kwargs
    assert subagent.sessions == [None]
    assert subagent.inputs[0]["query"] == "run task"
    assert re.fullmatch(
        r"parent_session_sub_browser_agent_[0-9a-f]{8}",
        subagent.inputs[0]["conversation_id"],
    )
