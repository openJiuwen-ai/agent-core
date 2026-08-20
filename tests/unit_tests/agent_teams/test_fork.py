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
from openjiuwen.agent_teams.runtime.metadata import TEAMS_KEY
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.agent_teams.tools.database import (
    DatabaseConfig,
    DatabaseType,
    TeamDatabase,
)
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_member import CheckpointTool
from openjiuwen.agent_teams.tools.team_tools import SpawnTeammateTool
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
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
        enable_fork=True,
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
        record = agent_team.get_checkpoints()["code-ready"]
        assert record["count"] == 15
        assert record["description"] == ""
        assert record["created_by"] == "leader-1"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_publishes_checkpoint_event_when_member(self, db, messager):
        t = make_translator("cn")
        member = TeamBackend(
            team_name="test-team",
            member_name="counter-1",
            is_leader=False,
            db=db,
            messager=messager,
            enable_fork=True,
        )
        member._snapshot_length = lambda: 7
        member.publish_checkpoint_created = AsyncMock()
        tool = CheckpointTool(member, t)

        result = await tool.invoke({"name": "count-1", "description": "报数1完成"})

        assert result.success is True
        member.publish_checkpoint_created.assert_awaited_once()
        args = member.publish_checkpoint_created.await_args
        assert args.args == ("count-1", 7, "报数1完成")

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_does_not_publish_when_leader(self, agent_team):
        t = make_translator("cn")
        agent_team._snapshot_length = lambda: 15
        agent_team.publish_checkpoint_created = AsyncMock()
        tool = CheckpointTool(agent_team, t)

        await tool.invoke({"name": "code-ready"})

        agent_team.publish_checkpoint_created.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_invoke_duplicate_returns_error_with_creator_and_no_event(self, agent_team):
        t = make_translator("cn")
        agent_team._snapshot_length = lambda: 15
        agent_team._checkpoints["code-ready"] = {
            "count": 5, "description": "base done", "created_by": "counter-1",
        }
        agent_team.publish_checkpoint_created = AsyncMock()
        tool = CheckpointTool(agent_team, t)

        result = await tool.invoke({"name": "code-ready"})

        assert result.success is False
        assert "code-ready" in result.error
        assert "counter-1" in result.error
        assert "base done" in result.error
        agent_team.publish_checkpoint_created.assert_not_awaited()

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
        agent_team._store_checkpoint_fn = (
            lambda n, c, description="", created_by=None: stored.update({n: (c, description, created_by)})
        )
        agent_team.store_checkpoint("ck", 7, description="desc", created_by="dev-1")
        assert stored["ck"] == (7, "desc", "dev-1")

    @pytest.mark.level0
    def test_store_checkpoint_fallback(self, agent_team):
        agent_team._store_checkpoint_fn = None
        agent_team.store_checkpoint("fb", 3)
        record = agent_team.get_checkpoints()["fb"]
        assert record["count"] == 3
        assert record["description"] == ""
        assert record["created_by"] == "leader-1"

    @pytest.mark.level0
    def test_store_checkpoint_returns_conflict_record_on_duplicate(self, agent_team):
        agent_team._store_checkpoint_fn = None
        agent_team._checkpoints["code-ready"] = {
            "count": 5, "description": "base done", "created_by": "counter-1",
        }
        conflict = agent_team.store_checkpoint("code-ready", 99, created_by="leader-1")
        assert conflict == {"count": 5, "description": "base done", "created_by": "counter-1"}
        assert agent_team.get_checkpoints()["code-ready"]["count"] == 5  # unchanged

    @pytest.mark.level1
    def test_store_checkpoint_returns_none_on_success(self, agent_team):
        agent_team._store_checkpoint_fn = None
        assert agent_team.store_checkpoint("fresh", 3) is None
        assert agent_team.get_checkpoints()["fresh"]["count"] == 3

    @pytest.mark.level0
    def test_list_checkpoints_prefers_wired_namespace(self, agent_team):
        agent_team.set_checkpoint_list_fn(lambda: {"a": {"count": 1, "description": "d", "created_by": "dev-1"}})
        checkpoints = agent_team.list_checkpoints()
        assert checkpoints["a"]["count"] == 1
        assert checkpoints["a"]["description"] == "d"

    @pytest.mark.level1
    def test_list_checkpoints_falls_back_to_local(self, agent_team):
        agent_team._checkpoint_list_fn = None
        agent_team._checkpoints = {"b": {"count": 2, "description": "", "created_by": ""}}
        assert agent_team.list_checkpoints()["b"]["count"] == 2


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
        yield SpawnTeammateTool(agent_team, t, fork_enabled=True)

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
        agent.react_agent = MagicMock()
        agent.react_agent.context_engine = engine
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

        engine = agent.react_agent.context_engine
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
        ctx = agent.react_agent.context_engine.get_context.return_value
        ctx.set_messages.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_skip_split_equals_len(self):
        from openjiuwen.agent_teams.fork_compact import compact_context

        msgs = [UserMessage(content=f"m{i}") for i in range(5)]
        agent = self._make_mock_agent(msgs)
        await compact_context(agent, split_at=5)
        ctx = agent.react_agent.context_engine.get_context.return_value
        ctx.set_messages.assert_not_called()


# ── _on_teammate_created fork resolution (assembly path) ────────────────────


class TestOnTeammateCreatedFork:
    """Assembly-path tests for ``TeamAgent._on_teammate_created`` fork
    resolution.

    These guard the runtime wiring that unit-level ``ForkContext`` tests
    cannot see: live / boolean / named / compact fork handling, the
    missing-``else``-branch crash regression, and the silent no-fork
    fallback when the fork source cannot be resolved.
    """

    # Native context: 1 SystemMessage + 4 conversation messages.
    # After SystemMessage stripping ForkContext captures 4 messages.
    _ROLES = ["system", "user", "assistant", "user", "assistant"]
    _MESSAGE_COUNT = 4

    @classmethod
    def _make_native(cls):
        """Return a mock DeepAgent whose context has cls._ROLES messages."""
        native = MagicMock()
        msgs = []
        for i, role in enumerate(cls._ROLES):
            if role == "system":
                msgs.append(SystemMessage(content="sys"))
            elif role == "user":
                msgs.append(UserMessage(content=f"u{i}"))
            else:
                msgs.append(AssistantMessage(content=f"a{i}"))
        native.get_current_context = MagicMock(return_value=msgs)
        return native

    @classmethod
    def _make_agent(
        cls,
        fork_info,
        *,
        checkpoints=None,
        source=None,
    ):
        """Build a TeamAgent with its runtime parts mocked.

        Args:
            fork_info: What ``consume_fork_on_spawn`` returns (None = no fork).
            checkpoints: The leader's ``_named_checkpoints`` mapping.
            source: When given, register an in-process spawned member under
                this name so ``_resolve_fork_native`` resolves it.
        """
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent

        agent = object.__new__(TeamAgent)
        agent._configurator = MagicMock()
        agent._configurator.member_name = "leader-1"
        agent._configurator.resources = MagicMock()
        agent._configurator.resources.harness = MagicMock()
        agent._configurator.resources.harness.get_deep_agent = MagicMock(
            return_value=cls._make_native()
        )
        agent._configurator.team_backend = MagicMock()
        agent._configurator.team_backend.consume_fork_on_spawn = MagicMock(
            return_value=fork_info
        )
        agent._configurator.message_manager = AsyncMock()
        agent._named_checkpoints = dict(checkpoints or {})
        agent._spawn_manager = MagicMock()
        agent._spawn_manager.spawned_handles = {}
        if source is not None:
            handle = MagicMock()
            handle.agent_ref = MagicMock()
            handle.agent_ref.resources = MagicMock()
            handle.agent_ref.resources.harness = MagicMock()
            handle.agent_ref.resources.harness.get_deep_agent = MagicMock(
                return_value=cls._make_native()
            )
            agent._spawn_manager.spawned_handles[source] = handle
        agent._spawn_manager.build_context_from_db = AsyncMock(
            return_value=TeamRuntimeContext(
                role=TeamRole.TEAMMATE,
                member_name="rectangle-dev",
            )
        )
        agent._spawn_manager.spawn_teammate = AsyncMock(return_value=None)
        return agent

    async def _run(self, agent, member="rectangle-dev"):
        """Run ``_on_teammate_created`` and return the injected ``fork_from``."""
        token = set_session_id("fork-assembly-test")
        try:
            await agent._on_teammate_created(member)
        finally:
            reset_session_id(token)
        return agent._spawn_manager.spawn_teammate.call_args.kwargs["fork_from"]

    def _built_ctx(self, agent):
        """Return the TeamRuntimeContext the run handed to ``spawn_teammate``."""
        return agent._spawn_manager.spawn_teammate.call_args.args[0]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_live_fork_string_true_injects_full_context(self):
        agent = self._make_agent(
            {"fork": "true", "since": None, "source": None, "compact": False}
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == self._MESSAGE_COUNT
        assert fork_from.compact_split is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_source_defaults_to_leader_name(self):
        """Omitting fork_source records the leader as the conversion source."""
        agent = self._make_agent(
            {"fork": "true", "since": None, "source": None, "compact": False}
        )
        await self._run(agent)
        assert self._built_ctx(agent).fork_source == "leader-1"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_source_records_the_named_source(self):
        agent = self._make_agent(
            {"fork": "true", "since": None, "source": "reader", "compact": False},
            source="reader",
        )
        await self._run(agent)
        assert self._built_ctx(agent).fork_source == "reader"

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_no_fork_leaves_fork_source_unset(self):
        """A plain spawn (no fork_info) must not set fork_source."""
        agent = self._make_agent(None)
        await self._run(agent)
        assert getattr(self._built_ctx(agent), "fork_source", None) is None


    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_live_fork_boolean_true_injects_full_context(self):
        agent = self._make_agent(
            {"fork": True, "since": None, "source": None, "compact": False}
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == self._MESSAGE_COUNT

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_named_fork_truncates_to_checkpoint(self):
        agent = self._make_agent(
            {"fork": "code-ready", "since": None, "source": None, "compact": False},
            checkpoints={"code-ready": {"count": 2}},
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == 2
        assert fork_from.compact_split is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_named_fork_missing_falls_back_to_full(self):
        agent = self._make_agent(
            {"fork": "no-such", "since": None, "source": None, "compact": False},
            checkpoints={},
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == self._MESSAGE_COUNT

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_named_fork_with_compact_sets_split(self):
        agent = self._make_agent(
            {"fork": "code-ready", "since": None, "source": None, "compact": True},
            checkpoints={"code-ready": {"count": 2}},
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == self._MESSAGE_COUNT
        assert fork_from.compact_split == 2

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_compact_without_named_ignored(self):
        agent = self._make_agent(
            {"fork": "true", "since": None, "source": None, "compact": True},
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert fork_from.compact_split is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_no_fork_injects_none(self):
        agent = self._make_agent(None)
        fork_from = await self._run(agent)
        assert fork_from is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_unresolvable_source_skips_fork(self):
        agent = self._make_agent(
            {"fork": "true", "since": None, "source": "base-designer", "compact": False},
        )
        fork_from = await self._run(agent)
        assert fork_from is None

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_resolvable_source_injects_full_context(self):
        agent = self._make_agent(
            {"fork": "true", "since": None, "source": "shape-base", "compact": False},
            source="shape-base",
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == self._MESSAGE_COUNT

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_source_equal_leader_uses_own_native(self):
        agent = self._make_agent(
            {"fork": "true", "since": None, "source": "leader-1", "compact": False},
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == self._MESSAGE_COUNT

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_missing_checkpoint_name_notifies_leader_with_available(self):
        """A wrong fork name is surfaced to the leader, not silently ignored."""
        agent = self._make_agent(
            {"fork": "no-such", "since": None, "source": None, "compact": False},
            checkpoints={"code-ready": {"count": 2}},
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        assert len(fork_from.messages) == self._MESSAGE_COUNT  # full-context fallback
        agent._configurator.message_manager.send_message.assert_awaited_once()
        content = agent._configurator.message_manager.send_message.await_args.kwargs["content"]
        assert "no-such" in content
        assert "code-ready" in content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_resolved_checkpoint_name_does_not_notify(self):
        agent = self._make_agent(
            {"fork": "code-ready", "since": None, "source": None, "compact": False},
            checkpoints={"code-ready": {"count": 2}},
        )
        await self._run(agent)
        agent._configurator.message_manager.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_source_mismatch_notifies_leader_and_falls_back_to_full(self):
        """A checkpoint owned by a member other than fork_source is surfaced and
        the fork falls back to full context (the foreign index is not applied)."""
        agent = self._make_agent(
            {"fork": "code-ready", "since": None, "source": None, "compact": False},
            checkpoints={"code-ready": {"count": 2, "description": "", "created_by": "counter-1"}},
        )
        fork_from = await self._run(agent)
        assert isinstance(fork_from, ForkContext)
        # ckpt_idx cleared on mismatch → full context, NOT truncated to foreign count 2.
        assert len(fork_from.messages) == self._MESSAGE_COUNT
        agent._configurator.message_manager.send_message.assert_awaited_once()
        content = agent._configurator.message_manager.send_message.await_args.kwargs["content"]
        assert "code-ready" in content
        assert "counter-1" in content

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_fork_capture_failure_degrades_to_no_inheritance(self, monkeypatch):
        """A fork-capture failure must not abort the member spawn.

        The member is launched without inherited context and the failure is
        logged, instead of leaving the member UNSTARTED with a stalled
        message delivery.
        """
        agent = self._make_agent(
            {"fork": "code-ready", "since": None, "source": None, "compact": False},
            checkpoints={"code-ready": {"count": 2}},
        )

        def _boom(*args, **kwargs):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="cannot find context 'default_context_id' in session 's'",
            )

        monkeypatch.setattr(ForkContext, "from_agent", classmethod(_boom))

        fork_from = await self._run(agent)
        assert fork_from is None
        agent._spawn_manager.spawn_teammate.assert_awaited()


# ── ForkContext persisted-session fallback ──────────────────────────────────


class _ContextStateStub:
    """Minimal session mimicking the child-session state API.

    The real child session stores the engine's saved context under the
    ``"context"`` key: ``get_state("context")`` returns
    ``{context_id: {"messages": [...]}}``.
    """

    def __init__(self, context_state: dict) -> None:
        self.state: dict = {"context": context_state}

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


class TestForkContextPersistedFallback:
    """``ForkContext.from_agent`` recovers when the source context is not
    materialized in the live engine pool (e.g. after pause/resume rebuilds the
    native) by reading the source's persisted child-session context state.
    """

    @staticmethod
    def _make_agent(states=None, *, loop_session=None):
        agent = MagicMock()
        agent.get_current_context = MagicMock(
            side_effect=build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="cannot find context 'default_context_id' in session 's'",
            )
        )
        if loop_session is not None:
            agent.loop_session = loop_session
        elif states is not None:
            agent.loop_session = _ContextStateStub(states)
        else:
            agent.loop_session = None
        return agent

    @pytest.mark.level0
    def test_from_agent_falls_back_to_persisted_messages(self):
        agent = self._make_agent({
            "default_context_id": {
                "messages": [
                    SystemMessage(content="sys"),
                    UserMessage(content="count 1"),
                    AssistantMessage(content="reported 1"),
                ]
            }
        })
        ctx = ForkContext.from_agent(agent)
        decoded = ctx.to_messages()
        # SystemMessage stripped; user/assistant conversation preserved.
        assert len(decoded) == 2
        assert decoded[0].content == "count 1"
        assert decoded[1].content == "reported 1"

    @pytest.mark.level0
    def test_from_agent_fallback_respects_checkpoint_truncation(self):
        agent = self._make_agent({
            "default_context_id": {
                "messages": [
                    UserMessage(content="m0"),
                    AssistantMessage(content="m1"),
                    UserMessage(content="m2"),
                ]
            }
        })
        ctx = ForkContext.from_agent(agent, checkpoint=2)
        decoded = ctx.to_messages()
        assert [m.content for m in decoded] == ["m0", "m1"]

    @pytest.mark.level1
    def test_from_agent_reraises_when_no_persisted_state(self):
        agent = self._make_agent(loop_session=None)
        with pytest.raises(Exception) as exc_info:
            ForkContext.from_agent(agent)
        assert "cannot find context" in str(exc_info.value)

    @pytest.mark.level0
    def test_from_agent_fallback_decodes_json_dict_messages(self):
        """Persisted messages stored as json dicts are restored to messages.

        The checkpointer currently round-trips BaseMessage objects, but a
        future serializer may persist them as dicts; the fallback must not
        silently degrade to no-inheritance in that case.
        """
        raw = [
            UserMessage(content="count 1").model_dump(mode="json"),
            AssistantMessage(content="reported 1").model_dump(mode="json"),
        ]
        agent = self._make_agent({"default_context_id": {"messages": raw}})

        ctx = ForkContext.from_agent(agent)
        decoded = ctx.to_messages()
        assert [m.content for m in decoded] == ["count 1", "reported 1"]

    @pytest.mark.level1
    def test_from_agent_fallback_reraises_on_malformed_messages(self):
        """A non-message, non-dict element fails the fallback and re-raises."""
        agent = self._make_agent(
            {"default_context_id": {"messages": [UserMessage(content="ok"), "junk"]}}
        )
        with pytest.raises(Exception) as exc_info:
            ForkContext.from_agent(agent)
        assert "cannot find context" in str(exc_info.value)

    @pytest.mark.level0
    def test_fallback_path_also_closes_tool_call_boundary(self):
        """The persisted-session fallback path applies the same boundary fix."""
        tool_call = ToolCall(id="tc1", type="function", name="checkpoint", arguments="{}")
        raw = [
            UserMessage(content="task").model_dump(mode="json"),
            AssistantMessage(content="1", tool_calls=[tool_call]).model_dump(mode="json"),
            ToolMessage(tool_call_id="tc1", content="saved").model_dump(mode="json"),
        ]
        agent = self._make_agent({"default_context_id": {"messages": raw}})

        ctx = ForkContext.from_agent(agent, checkpoint=2)
        decoded = ctx.to_messages()
        assert len(decoded) == 3
        assert isinstance(decoded[-1], ToolMessage)


# ── ForkContext checkpoint boundary closure ─────────────────────────────────


class TestForkContextCheckpointBoundary:
    """Truncation at a checkpoint must carry the closing ToolMessage(s) across
    the boundary so the injected context has no dangling tool call (which the
    product rail would mark as ``[工具执行被中断]``).
    """

    @staticmethod
    def _checkpoint_call(call_id: str = "tc1", name: str = "checkpoint") -> ToolCall:
        return ToolCall(id=call_id, type="function", name=name, arguments="{}")

    @pytest.mark.level0
    def test_boundary_carries_tool_result_across(self):
        tool_call = self._checkpoint_call()
        msgs = [
            UserMessage(content="task"),
            AssistantMessage(content="1", tool_calls=[tool_call]),
            ToolMessage(tool_call_id="tc1", content="Checkpoint 'count-1' saved at message 2"),
        ]
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=2)
        decoded = ctx.to_messages()
        assert len(decoded) == 3
        assert decoded[1].tool_calls and decoded[1].tool_calls[0].name == "checkpoint"
        assert isinstance(decoded[2], ToolMessage)
        assert decoded[2].tool_call_id == "tc1"

    @pytest.mark.level0
    def test_boundary_carries_all_tool_results_for_multi_call(self):
        call_a = self._checkpoint_call(call_id="ta")
        call_b = self._checkpoint_call(call_id="tb", name="send_message")
        msgs = [
            AssistantMessage(content="", tool_calls=[call_a, call_b]),
            ToolMessage(tool_call_id="ta", content="a result"),
            ToolMessage(tool_call_id="tb", content="b result"),
        ]
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=1)
        decoded = ctx.to_messages()
        assert len(decoded) == 3
        assert [getattr(m, "tool_call_id", None) for m in decoded[1:]] == ["ta", "tb"]

    @pytest.mark.level1
    def test_boundary_no_extension_after_plain_response(self):
        msgs = [
            UserMessage(content="task"),
            AssistantMessage(content="ok"),
        ]
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=2)
        assert len(ctx.to_messages()) == 2

    @pytest.mark.level1
    def test_boundary_no_extension_when_result_missing(self):
        tool_call = self._checkpoint_call()
        msgs = [
            UserMessage(content="task"),
            AssistantMessage(content="1", tool_calls=[tool_call]),
        ]
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=2)
        assert len(ctx.to_messages()) == 2

    @pytest.mark.level1
    def test_boundary_no_extension_when_next_not_tool_message(self):
        tool_call = self._checkpoint_call()
        msgs = [
            UserMessage(content="task"),
            AssistantMessage(content="1", tool_calls=[tool_call]),
            UserMessage(content="next turn"),
        ]
        ctx = ForkContext.from_agent(_fake_agent(msgs), checkpoint=2)
        assert len(ctx.to_messages()) == 2


# ── TeamAgent checkpoint persistence ────────────────────────────────────────


class _StubSession:
    """Minimal session mimicking agent_team session's state API."""

    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


class TestTeamAgentCheckpointPersistence:
    """Assembly-path tests for ``TeamAgent.set_checkpoint`` persistence.

    Verifies the leader mirrors checkpoints into the session per-team
    namespace (the durable copy read back by cold recovery), that an
    unbound session degrades gracefully, and that non-leader members only
    mutate the in-memory mapping.
    """

    @classmethod
    def _make_agent(cls, *, role, team_session, team_name="test-team"):
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent

        agent = object.__new__(TeamAgent)
        agent._configurator = MagicMock()
        agent._configurator.role = role
        agent._configurator.team_name = team_name
        agent._session_manager = MagicMock()
        agent._session_manager.team_session = team_session
        agent._named_checkpoints = {}
        return agent

    def _bucket_checkpoints(self, session) -> dict:
        return session.state[TEAMS_KEY]["test-team"]["checkpoints"]

    @pytest.mark.level0
    def test_leader_set_checkpoint_mirrors_full_mapping_into_session(self):
        from openjiuwen.agent_teams.schema.team import TeamRole

        session = _StubSession()
        agent = self._make_agent(role=TeamRole.LEADER, team_session=session)

        agent.set_checkpoint("code-ready", 5, description="base done", created_by="dev-1")
        agent.set_checkpoint("refactor-done", 12)

        assert agent._named_checkpoints == {
            "code-ready": {"count": 5, "description": "base done", "created_by": "dev-1"},
            "refactor-done": {"count": 12, "description": "", "created_by": ""},
        }
        assert self._bucket_checkpoints(session) == {
            "code-ready": {"count": 5, "description": "base done", "created_by": "dev-1"},
            "refactor-done": {"count": 12, "description": "", "created_by": ""},
        }

    @pytest.mark.level1
    def test_leader_set_checkpoint_unbound_session_degrades_gracefully(self):
        from openjiuwen.agent_teams.schema.team import TeamRole

        agent = self._make_agent(role=TeamRole.LEADER, team_session=None)
        agent.set_checkpoint("code-ready", 5)
        assert agent._named_checkpoints == {
            "code-ready": {"count": 5, "description": "", "created_by": ""}
        }

    @pytest.mark.level1
    def test_teammate_set_checkpoint_does_not_touch_session(self):
        from openjiuwen.agent_teams.schema.team import TeamRole

        session = _StubSession()
        agent = self._make_agent(role=TeamRole.TEAMMATE, team_session=session)

        agent.set_checkpoint("code-ready", 5)

        assert agent._named_checkpoints == {
            "code-ready": {"count": 5, "description": "", "created_by": ""}
        }
        assert TEAMS_KEY not in session.state

    @pytest.mark.level0
    def test_same_member_duplicate_name_rejected_not_overwritten(self):
        from openjiuwen.agent_teams.schema.team import TeamRole

        agent = self._make_agent(role=TeamRole.LEADER, team_session=None)
        assert agent.set_checkpoint("code-ready", 5, created_by="dev-1") is None

        conflict = agent.set_checkpoint("code-ready", 99, created_by="dev-1")

        assert conflict == {"count": 5, "description": "", "created_by": "dev-1"}
        assert agent._named_checkpoints["code-ready"]["count"] == 5  # unchanged

    @pytest.mark.level1
    def test_cross_member_duplicate_name_rejected_globally(self):
        from openjiuwen.agent_teams.schema.team import TeamRole

        agent = self._make_agent(role=TeamRole.LEADER, team_session=None)
        assert agent.set_checkpoint("code-ready", 5, created_by="dev-1") is None

        conflict = agent.set_checkpoint("code-ready", 99, created_by="dev-2")

        assert conflict is not None
        assert conflict["created_by"] == "dev-1"
        assert agent._named_checkpoints["code-ready"]["count"] == 5  # unchanged


# ── enable_fork capability gate ────────────────────────────────────────────
#
# One flag shapes three surfaces at once (F_75): the ``checkpoint`` tool, the
# fork properties on ``spawn_teammate``'s schema, and the fork section of its
# description. They are asserted together on purpose — a leader that reads
# about ``fork`` but has no ``fork`` property to fill deliberates over a
# mechanism it cannot invoke.

_FORK_PROPS = {"fork", "fork_source", "compact"}


@pytest_asyncio.fixture
async def fork_disabled_team(db, messager):
    """A TeamBackend with the default (closed) fork capability."""
    await db.team.create_team(
        team_name="no-fork-team",
        display_name="No Fork Team",
        leader_member_name="leader-1",
    )
    yield TeamBackend(
        team_name="no-fork-team",
        member_name="leader-1",
        is_leader=True,
        db=db,
        messager=messager,
    )


def _spawn_schema_props(tools) -> set[str]:
    """Property names on the assembled ``spawn_teammate`` schema."""
    spawn = next(tool for tool in tools if tool.card.name == "spawn_teammate")
    return set(spawn.card.input_params["properties"])


def _spawn_desc(tools) -> str:
    """Rendered description of the assembled ``spawn_teammate`` tool."""
    return next(tool for tool in tools if tool.card.name == "spawn_teammate").card.description


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize("role", ["leader", "teammate"])
async def test_fork_disabled_hides_every_fork_surface(fork_disabled_team, lang, role):
    """enable_fork=False removes tool, schema properties, and prose alike."""
    from openjiuwen.agent_teams.tools.tool_factory import create_team_tools

    tools = create_team_tools(role=role, agent_team=fork_disabled_team, lang=lang)
    assert "checkpoint" not in {tool.card.name for tool in tools}
    assert "list_checkpoints" not in {tool.card.name for tool in tools}
    if role != "leader":
        return
    assert _spawn_schema_props(tools) & _FORK_PROPS == set()
    desc = _spawn_desc(tools)
    assert "fork" not in desc.lower()
    assert "{{" not in desc


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize("lang", ["cn", "en"])
@pytest.mark.parametrize("role", ["leader", "teammate"])
async def test_fork_enabled_exposes_every_fork_surface(agent_team, lang, role):
    """enable_fork=True wires checkpoint, the fork properties, and the prose."""
    from openjiuwen.agent_teams.tools.tool_factory import create_team_tools

    tools = create_team_tools(role=role, agent_team=agent_team, lang=lang)
    assert "checkpoint" in {tool.card.name for tool in tools}
    if role != "leader":
        return
    assert "list_checkpoints" in {tool.card.name for tool in tools}
    assert _FORK_PROPS <= _spawn_schema_props(tools)
    desc = _spawn_desc(tools)
    assert "fork_source" in desc
    assert "{{" not in desc


@pytest.mark.asyncio
@pytest.mark.level0
async def test_checkpoint_invoke_rejected_when_fork_disabled(fork_disabled_team):
    """MCP clients bypass the schema, so ``invoke`` re-checks the gate."""
    tool = CheckpointTool(fork_disabled_team, make_translator("cn"))
    result = await tool.invoke({"name": "code-ready"})
    assert not result.success
    assert "enable_fork" in result.error


@pytest.mark.asyncio
@pytest.mark.level0
@pytest.mark.parametrize(
    "fork_args",
    [
        {"fork": True},
        {"fork": "code-ready", "compact": True},
        {"fork_source": "reader"},
    ],
)
async def test_spawn_teammate_rejects_fork_args_when_disabled(fork_disabled_team, fork_args):
    """A fork argument smuggled past the schema fails before any member row."""
    tool = SpawnTeammateTool(fork_disabled_team, make_translator("cn"))
    result = await tool.invoke({
        "member_name": "dev-9",
        "display_name": "Dev 9",
        "desc": "helper",
        **fork_args,
    })
    assert not result.success
    assert "enable_fork" in result.error
    assert not await fork_disabled_team.db.member.member_exists("dev-9", "no-fork-team")


@pytest.mark.asyncio
@pytest.mark.level1
async def test_spawn_teammate_still_works_without_fork_args(fork_disabled_team):
    """The gate rejects fork arguments only — ordinary spawns are untouched."""
    tool = SpawnTeammateTool(fork_disabled_team, make_translator("cn"))
    result = await tool.invoke({
        "member_name": "dev-8",
        "display_name": "Dev 8",
        "desc": "helper",
    })
    assert result.success
    assert fork_disabled_team.consume_fork_on_spawn("dev-8") is None
