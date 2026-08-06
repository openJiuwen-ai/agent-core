# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for fork, checkpoint, and compact features."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import (
    reset_session_id,
    set_session_id,
)
from openjiuwen.agent_teams.fork import ForkContext
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_member import CheckpointTool
from openjiuwen.agent_teams.tools.team_tools import SpawnTeammateTool
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    SystemMessage,
    UserMessage,
)
from openjiuwen.harness.tools.base_tool import ToolOutput

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db_config() -> DatabaseConfig:
    return DatabaseConfig(
        db_type=DatabaseType.SQLITE, connection_string=":memory:"
    )


@pytest_asyncio.fixture
async def db(db_config):
    token = set_session_id("fork-test-session")
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
async def agent_team(db, messager):
    await db.team.create_team(
        team_name="test-team",
        display_name="Test Team",
        leader_member_name="leader-1",
    )
    t = TeamBackend(
        team_name="test-team",
        member_name="leader-1",
        is_leader=True,
        db=db,
        messager=messager,
    )
    t._snapshot_length = lambda: 0
    yield t


# ── helpers ────────────────────────────────────────────────────────────────


def _make_messages(*roles: str) -> list:
    """Build a list of messages with the given roles for ForkContext tests."""
    msgs = []
    for role in roles:
        if role == "system":
            msgs.append(SystemMessage(content="sys"))
        elif role == "user":
            msgs.append(UserMessage(content="hello"))
        elif role == "assistant":
            msgs.append(AssistantMessage(content="ok"))
    return msgs


def _fake_agent(messages: list):
    """Return a MagicMock with ``get_current_context`` returning *messages*."""
    agent = MagicMock()
    agent.get_current_context = MagicMock(return_value=messages)
    return agent


# ── ForkContext ────────────────────────────────────────────────────────────


class TestForkContext:
    """Unit tests for ForkContext.from_agent and helpers."""

    @pytest.mark.level0
    def test_full_context_no_checkpoint(self):
        msgs = _make_messages("user", "assistant", "user")
        ctx = ForkContext.from_agent(_fake_agent(msgs))
        assert not ctx.is_empty()
        assert len(ctx.messages) == 3
        assert ctx.messages[0]["role"] == "user"
        assert ctx.compact_split is None

    @pytest.mark.level0
    def test_checkpoint_truncation(self):
        msgs = _make_messages("user", "assistant", "user", "assistant", "user")
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=2)
        assert len(ctx.messages) == 2
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[1]["role"] == "assistant"

    @pytest.mark.level1
    def test_checkpoint_zero(self):
        msgs = _make_messages("user", "assistant")
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=0)
        assert ctx.is_empty()

    @pytest.mark.level1
    def test_checkpoint_equal_to_len(self):
        msgs = _make_messages("user", "assistant")
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=2)
        assert len(ctx.messages) == 2  # not truncated (idx >= len)

    @pytest.mark.level1
    def test_checkpoint_out_of_bounds(self):
        msgs = _make_messages("user", "assistant")
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=999)
        assert len(ctx.messages) == 2  # not truncated

    @pytest.mark.level0
    def test_system_messages_stripped(self):
        msgs = _make_messages(
            "system", "user", "assistant", "system", "user",
        )
        ctx = ForkContext.from_agent(_fake_agent(msgs))
        roles = [m["role"] for m in ctx.messages]
        assert "system" not in roles
        assert roles == ["user", "assistant", "user"]

    @pytest.mark.level0
    def test_to_messages_roundtrip(self):
        msgs = _make_messages("user", "assistant")
        ctx = ForkContext.from_agent(_fake_agent(msgs))
        decoded = ctx.to_messages()
        assert len(decoded) == 2
        assert isinstance(decoded[0], UserMessage)
        assert isinstance(decoded[1], AssistantMessage)

    @pytest.mark.level0
    def test_is_empty_true(self):
        assert ForkContext(messages=[]).is_empty()

    @pytest.mark.level0
    def test_is_empty_false(self):
        assert not ForkContext(messages=[{"role": "user"}]).is_empty()

    @pytest.mark.level0
    def test_compact_split_default_is_none(self):
        ctx = ForkContext(messages=[{"role": "user"}])
        assert ctx.compact_split is None


# ── CheckpointTool ─────────────────────────────────────────────────────────


class TestCheckpointTool:
    """Unit tests for the CheckpointTool."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_invoke_saves_checkpoint(self, agent_team):
        t = make_translator("cn")
        agent_team._snapshot_length = lambda: 15
        tool = CheckpointTool(agent_team, t)
        result = await tool.invoke({"name": "code-ready"})
        assert result.success is True
        assert result.data["name"] == "code-ready"
        assert result.data["message_count"] == 15
        assert agent_team.get_checkpoints()["code-ready"] == 15

    @pytest.mark.level0
    def test_map_result_success(self, agent_team):
        t = make_translator("cn")
        tool = CheckpointTool(agent_team, t)
        out = ToolOutput(
            success=True,
            data={"name": "cp", "message_count": 42},
        )
        text = tool.map_result(out)
        assert "cp" in text

    @pytest.mark.level1
    def test_map_result_failure(self, agent_team):
        t = make_translator("cn")
        tool = CheckpointTool(agent_team, t)
        out = ToolOutput(success=False, error="boom")
        text = tool.map_result(out)
        assert "boom" in text or "Failed" in text


# ── TeamBackend fork methods ───────────────────────────────────────────────


class TestTeamBackendFork:
    """Unit tests for TeamBackend fork / checkpoint management."""

    @pytest.mark.level0
    def test_mark_and_consume(self, agent_team):
        agent_team.mark_fork_on_spawn(
            "dev-1", "base-ready", fork_source="reader", compact=True,
        )
        info = agent_team.consume_fork_on_spawn("dev-1")
        assert info == {
            "fork": "base-ready",
            "since": None,
            "source": "reader",
            "compact": True,
        }
        # consumed — second lookup is None
        assert agent_team.consume_fork_on_spawn("dev-1") is None

    @pytest.mark.level1
    def test_consume_unknown(self, agent_team):
        assert agent_team.consume_fork_on_spawn("nobody") is None

    @pytest.mark.level1
    def test_snapshot_context_length_no_callback(self, agent_team):
        agent_team._snapshot_length = None
        assert agent_team.snapshot_context_length() == 0

    @pytest.mark.level0
    def test_store_checkpoint_with_callback(self, agent_team):
        stored = {}
        agent_team._store_checkpoint_fn = lambda n, c: stored.update({n: c})
        agent_team.store_checkpoint("ck", 7)
        assert stored["ck"] == 7

    @pytest.mark.level0
    def test_store_checkpoint_fallback(self, agent_team):
        agent_team._store_checkpoint_fn = None
        agent_team.store_checkpoint("fb", 3)
        assert agent_team.get_checkpoints()["fb"] == 3


# ── SpawnTeammateTool fork params ──────────────────────────────────────────


class TestSpawnTeammateForkParams:
    """Verify that SpawnTeammateTool passes fork params through."""

    @pytest_asyncio.fixture
    async def tool(self, agent_team):
        t = make_translator("cn")
        # pre-create members table row so spawn_member won't fail
        await agent_team.db.member.create_member(
            member_name="dev-1",
            team_name="test-team",
            display_name="Dev",
            agent_card='{"name":"x"}',
            status=MemberStatus.READY,
        )
        yield SpawnTeammateTool(agent_team, t)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_passes_fork_and_source(self, agent_team, tool):
        result = await tool.invoke({
            "member_name": "dev-2",
            "display_name": "Dev 2",
            "desc": "helper",
            "fork": True,
            "fork_source": "reader",
        })
        assert result.success
        info = agent_team.consume_fork_on_spawn("dev-2")
        assert info is not None
        assert info["fork"] is True
        assert info["source"] == "reader"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_passes_named_fork(self, agent_team, tool):
        result = await tool.invoke({
            "member_name": "dev-3",
            "display_name": "Dev 3",
            "desc": "helper",
            "fork": "code-ready",
        })
        assert result.success
        info = agent_team.consume_fork_on_spawn("dev-3")
        assert info is not None
        assert info["fork"] == "code-ready"
        assert info.get("since") is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_passes_compact(self, agent_team, tool):
        result = await tool.invoke({
            "member_name": "dev-4",
            "display_name": "Dev 4",
            "desc": "helper",
            "fork": "code-ready",
            "compact": True,
        })
        assert result.success
        info = agent_team.consume_fork_on_spawn("dev-4")
        assert info is not None
        assert info["compact"] is True

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_no_fork_does_not_mark(self, agent_team, tool):
        result = await tool.invoke({
            "member_name": "dev-5",
            "display_name": "Dev 5",
            "desc": "helper",
        })
        assert result.success
        assert agent_team.consume_fork_on_spawn("dev-5") is None


# ── compact_context ────────────────────────────────────────────────────────


class TestCompactContext:
    """Unit tests for compact_context."""

    @staticmethod
    def _make_mock_agent(messages):
        agent = MagicMock()
        agent.get_current_context = MagicMock(return_value=list(messages))
        ctx = MagicMock()
        ctx.set_messages = MagicMock()
        engine = MagicMock()
        engine.get_context = MagicMock(return_value=ctx)
        agent._react_agent = MagicMock()
        agent._react_agent.context_engine = engine
        model = MagicMock()
        model.invoke = AsyncMock(
            return_value=AssistantMessage(content="summary text")
        )
        agent.deep_config = MagicMock()
        agent.deep_config.model = model
        return agent

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_splits_and_replaces(self):
        from openjiuwen.agent_teams.fork_compact import compact_context

        agent = self._make_mock_agent(
            [UserMessage(content=f"m{i}") for i in range(15)]
        )
        await compact_context(agent, split_at=5)

        engine = agent._react_agent.context_engine
        engine.get_context.assert_called_once_with(
            context_id="default_context_id",
            session_id="default_session_id",
        )
        ctx = engine.get_context.return_value
        ctx.set_messages.assert_called_once()
        compacted = ctx.set_messages.call_args[0][0]
        assert len(compacted) == 11  # 1 summary + 10 recent
        assert isinstance(compacted[0], UserMessage)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_skip_split_at_zero(self):
        from openjiuwen.agent_teams.fork_compact import compact_context

        agent = self._make_mock_agent(
            [UserMessage(content=f"m{i}") for i in range(10)]
        )
        await compact_context(agent, split_at=0)
        agent.get_current_context.assert_called_once()
        ctx = agent._react_agent.context_engine.get_context.return_value
        ctx.set_messages.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_skip_split_equals_len(self):
        from openjiuwen.agent_teams.fork_compact import compact_context

        msgs = [UserMessage(content=f"m{i}") for i in range(5)]
        agent = self._make_mock_agent(msgs)
        await compact_context(agent, split_at=5)
        ctx = agent._react_agent.context_engine.get_context.return_value
        ctx.set_messages.assert_not_called()
