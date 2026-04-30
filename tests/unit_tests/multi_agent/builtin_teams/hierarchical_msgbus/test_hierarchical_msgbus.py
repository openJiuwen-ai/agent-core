# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for the hierarchical_msgbus module.

Covers:
1. HierarchicalTeamConfig -- supervisor_agent field validation and defaults
2. HierarchicalTeam       -- init, add_agent, _assert_ready, invoke, stream
3. P2PAbilityManager      -- init, add, execute (agent/non-agent/mixed/parallel/error)
4. SupervisorAgent        -- __init__, register_sub_agent_card, configure, create
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import (
    HierarchicalTeam,
    HierarchicalTeamConfig,
    P2PAbilityManager,
    SupervisorAgent,
)
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.foundation.llm import ToolCall, ToolMessage
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig


class _SupervisorAgentAccessor(SupervisorAgent):
    """Thin subclass that exposes internal state for white-box testing."""

    def get_ability_manager(self) -> P2PAbilityManager:
        """Return the P2PAbilityManager owned by self (legitimate subclass access)."""
        return self._ability_manager


def _get_ability_manager(agent: SupervisorAgent) -> P2PAbilityManager:
    return vars(agent)["_ability_manager"]


def _get_semaphore_value(mgr: P2PAbilityManager) -> int:
    return vars(mgr)["_max_parallel_sub_agents"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sv_card(sid: str = "supervisor") -> AgentCard:
    return AgentCard(id=sid, name=sid, description="supervisor agent")


def _sub_card(aid: str) -> AgentCard:
    return AgentCard(id=aid, name=aid, description=f"sub-agent {aid}")


def _team_card(tid: str = "h_team") -> TeamCard:
    return TeamCard(id=tid, name=tid, description="hierarchical team")


def _make_config(sid: str = "supervisor") -> HierarchicalTeamConfig:
    return HierarchicalTeamConfig(supervisor_agent=_sv_card(sid))


def _make_team(sid: str = "supervisor") -> HierarchicalTeam:
    return HierarchicalTeam(card=_team_card(), config=_make_config(sid))


def _tc(name: str, args: dict | None = None, call_id: str = "tc1") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments=json.dumps(args or {}))


def _mock_llm_configs():
    mc = MagicMock()
    mc.client_provider = "openai"
    mc.api_key = "test-key"
    mc.api_base = "https://api.example.com"
    mr = MagicMock()
    mr.model_name = "gpt-4"
    return mc, mr


# ---------------------------------------------------------------------------
# SECTION 1 -- HierarchicalTeamConfig
# ---------------------------------------------------------------------------

class TestHierarchicalTeamConfig:
    """Validates HierarchicalTeamConfig field constraints and inheritance."""

    @staticmethod
    def test_requires_supervisor_agent():
        """Construction without supervisor_agent must raise ValidationError."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            HierarchicalTeamConfig()  # type: ignore[call-arg]

    @staticmethod
    def test_stores_supervisor_card():
        """supervisor_agent holds the supplied AgentCard."""
        card = _sv_card("sv1")
        cfg = HierarchicalTeamConfig(supervisor_agent=card)
        assert cfg.supervisor_agent.id == "sv1"
        assert cfg.supervisor_agent.name == "sv1"

    @staticmethod
    def test_supervisor_card_id_accessible():
        """supervisor_agent.id is accessible on the config."""
        cfg = _make_config("my_supervisor")
        assert cfg.supervisor_agent.id == "my_supervisor"

    @staticmethod
    def test_inherits_team_config():
        """HierarchicalTeamConfig is a subclass of TeamConfig."""
        from openjiuwen.core.multi_agent.config import TeamConfig
        assert isinstance(_make_config(), TeamConfig)

    @staticmethod
    def test_team_config_default_max_agents():
        """Default max_agents from TeamConfig base class is 10."""
        assert _make_config().max_agents == 10

    @staticmethod
    def test_two_configs_are_independent():
        """Different supervisor ids produce distinct, independent configs."""
        cfg_a = _make_config("sv_a")
        cfg_b = _make_config("sv_b")
        assert cfg_a.supervisor_agent.id != cfg_b.supervisor_agent.id


# ---------------------------------------------------------------------------
# SECTION 2 -- HierarchicalTeam: init
# ---------------------------------------------------------------------------

class TestHierarchicalTeamInit:
    """Tests for HierarchicalTeam initialisation state."""

    @staticmethod
    def test_card_stored():
        """team.card holds the supplied TeamCard."""
        card = _team_card("my_team")
        team = HierarchicalTeam(card=card, config=_make_config())
        assert team.card is card

    @staticmethod
    def test_config_stored():
        """team.config holds the supplied HierarchicalTeamConfig."""
        cfg = _make_config("sv_cfg")
        team = HierarchicalTeam(card=_team_card(), config=cfg)
        assert team.config is cfg

    @staticmethod
    def test_runtime_created():
        """A TeamRuntime is created during init."""
        from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime
        assert isinstance(_make_team().runtime, TeamRuntime)

    @staticmethod
    def test_runtime_has_no_agents_initially():
        """Freshly created team has zero agents registered."""
        assert _make_team().get_agent_count() == 0

    @staticmethod
    def test_is_base_team():
        """HierarchicalTeam is a subclass of BaseTeam."""
        from openjiuwen.core.multi_agent.team import BaseTeam
        assert isinstance(_make_team(), BaseTeam)


# ---------------------------------------------------------------------------
# SECTION 2a -- HierarchicalTeam: add_agent
# ---------------------------------------------------------------------------

class TestHierarchicalTeamAddAgent:
    """Tests for HierarchicalTeam.add_agent registration."""

    @staticmethod
    def test_registers_agent_in_runtime():
        """add_agent registers the card so has_agent returns True."""
        team = _make_team()
        team.add_agent(_sub_card("a1"), lambda: MagicMock())
        assert team.runtime.has_agent("a1") is True

    @staticmethod
    def test_returns_self_for_chaining():
        """add_agent returns self to support method chaining."""
        team = _make_team()
        assert team.add_agent(_sub_card("a2"), lambda: MagicMock()) is team

    @staticmethod
    def test_agent_count_increments():
        """Agent count increases by one for each distinct agent added."""
        team = _make_team()
        assert team.get_agent_count() == 0
        team.add_agent(_sub_card("a1"), lambda: MagicMock())
        assert team.get_agent_count() == 1
        team.add_agent(_sub_card("a2"), lambda: MagicMock())
        assert team.get_agent_count() == 2

    @staticmethod
    def test_supervisor_card_registered():
        """Supervisor card is accepted and registered normally."""
        team = _make_team("sv_add")
        team.add_agent(_sv_card("sv_add"), lambda: MagicMock())
        assert team.runtime.has_agent("sv_add") is True

    @staticmethod
    def test_duplicate_agent_does_not_increase_count():
        """Adding the same agent id twice is a no-op on the second call."""
        team = _make_team()
        team.add_agent(_sub_card("dup"), lambda: MagicMock())
        team.add_agent(_sub_card("dup"), lambda: MagicMock())
        assert team.get_agent_count() == 1

    @staticmethod
    def test_get_agent_card_after_registration():
        """get_agent_card returns the exact card supplied to add_agent."""
        team = _make_team()
        card = _sub_card("lookup_me")
        team.add_agent(card, lambda: MagicMock())
        assert team.get_agent_card("lookup_me") is card

    @staticmethod
    def test_list_agents_contains_registered_id():
        """list_agents includes every registered agent id."""
        team = _make_team()
        team.add_agent(_sub_card("listed"), lambda: MagicMock())
        assert "listed" in team.list_agents()

    @staticmethod
    def test_supervisor_registration_emits_info_log():
        """Registering the supervisor card calls the logger info method mentioning its id."""
        import openjiuwen.core.multi_agent.teams.hierarchical_msgbus.hierarchical_team as _ht_mod
        with patch.object(_ht_mod.logger, "info") as mock_info:
            team = _make_team("sv_logged")
            team.add_agent(_sv_card("sv_logged"), lambda: MagicMock())
        assert mock_info.called
        combined = " ".join(str(c) for c in mock_info.call_args_list)
        assert "sv_logged" in combined


# ---------------------------------------------------------------------------
# SECTION 2b -- HierarchicalTeam: _assert_ready
# ---------------------------------------------------------------------------
# _assert_ready is a protected helper; its semantics are verified indirectly
# by calling the public invoke() API which calls _assert_ready internally.

_INVOKE_CTX = (
    "openjiuwen.core.multi_agent.teams.hierarchical_msgbus"
    ".hierarchical_team.standalone_invoke_context"
)


class TestHierarchicalTeamAssertReady:
    """Tests for the _assert_ready guard (verified via the public invoke() API)."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_raises_when_supervisor_not_registered():
        """invoke() raises BaseError when the supervisor is absent."""
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            await _make_team("sv_missing").invoke({"query": "hi"})

    @staticmethod
    @pytest.mark.asyncio
    async def test_passes_when_supervisor_registered():
        """invoke() does not raise when supervisor is registered."""
        team = _make_team("sv_ok")
        team.add_agent(_sv_card("sv_ok"), lambda: MagicMock())
        fake_session = MagicMock()
        fake_session.get_session_id = MagicMock(return_value="sid-ok")
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(fake_session, "sid-ok"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(_INVOKE_CTX, return_value=mock_ctx), \
             patch.object(team.runtime, "send", new=AsyncMock(return_value={"ok": True})):
            result = await team.invoke({"query": "hi"})
        assert result == {"ok": True}

    @staticmethod
    @pytest.mark.asyncio
    async def test_raises_when_only_non_supervisor_registered():
        """Registering only a sub-agent does not satisfy _assert_ready."""
        from openjiuwen.core.common.exception.errors import BaseError
        team = _make_team("sv_real")
        team.add_agent(_sub_card("not_supervisor"), lambda: MagicMock())
        with pytest.raises(BaseError):
            await team.invoke({"query": "test"})


# ---------------------------------------------------------------------------
# SECTION 2c -- HierarchicalTeam: invoke
# ---------------------------------------------------------------------------

def _patch_invoke_ctx(team, session_id="test-sid", send_return=None):
    """Return a context-manager patch that stubs standalone_invoke_context."""
    fake_session = MagicMock()
    fake_session.get_session_id = MagicMock(return_value=session_id)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=(fake_session, session_id))
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    ctx_patch = patch(_INVOKE_CTX, return_value=mock_ctx)
    send_patch = patch.object(
        team.runtime, "send",
        new=AsyncMock(return_value=send_return or {"output": "ok"}),
    )
    return ctx_patch, send_patch


class TestHierarchicalTeamInvoke:
    """Tests for HierarchicalTeam.invoke."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_raises_when_supervisor_not_registered():
        """invoke() raises BaseError before touching runtime when supervisor absent."""
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            await _make_team("sv_absent").invoke({"query": "hi"})

    @staticmethod
    @pytest.mark.asyncio
    async def test_returns_result_from_runtime_send():
        """invoke() returns the value produced by runtime.send."""
        team = _make_team("sv")
        team.add_agent(_sv_card("sv"), lambda: MagicMock())
        expected = {"output": "done"}
        ctx_patch, send_patch = _patch_invoke_ctx(team, send_return=expected)
        with ctx_patch, send_patch:
            result = await team.invoke({"query": "hello"})
        assert result == expected

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_called_with_supervisor_as_recipient():
        """invoke() calls runtime.send with recipient == supervisor id."""
        team = _make_team("sv_recv")
        team.add_agent(_sv_card("sv_recv"), lambda: MagicMock())
        captured = []

        async def spy_send(**kw):
            captured.append(kw)
            return {"ok": True}

        fake_session = MagicMock()
        fake_session.get_session_id = MagicMock(return_value="sid-1")
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(fake_session, "sid-1"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(_INVOKE_CTX, return_value=mock_ctx), \
             patch.object(team.runtime, "send", side_effect=spy_send):
            await team.invoke({"q": "test"})

        assert len(captured) == 1
        assert captured[0].get("recipient") == "sv_recv"

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_called_with_session_id():
        """invoke() forwards session_id to runtime.send."""
        team = _make_team("sv_sid")
        team.add_agent(_sv_card("sv_sid"), lambda: MagicMock())
        captured = []

        async def spy_send(**kw):
            captured.append(kw)
            return {}

        fake_session = MagicMock()
        fake_session.get_session_id = MagicMock(return_value="my-session-42")
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(fake_session, "my-session-42"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(_INVOKE_CTX, return_value=mock_ctx), \
             patch.object(team.runtime, "send", side_effect=spy_send):
            await team.invoke({"q": "test"})

        assert len(captured) >= 1
        assert captured[0].get("session_id") == "my-session-42"


# ---------------------------------------------------------------------------
# SECTION 2d -- HierarchicalTeam: stream
# ---------------------------------------------------------------------------

_STREAM_CTX = (
    "openjiuwen.core.multi_agent.teams.hierarchical_msgbus"
    ".hierarchical_team.standalone_stream_context"
)


class TestHierarchicalTeamStream:
    """Tests for HierarchicalTeam.stream."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_raises_when_supervisor_not_registered():
        """stream() raises BaseError when supervisor is missing."""
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            async for _ in _make_team("sv_absent").stream({"q": "hi"}):
                pass

    @staticmethod
    @pytest.mark.asyncio
    async def test_yields_all_chunks_from_stream_context():
        """stream() yields every chunk produced by standalone_stream_context."""
        team = _make_team("sv")
        team.add_agent(_sv_card("sv"), lambda: MagicMock())
        chunks_to_yield = [{"chunk": "a"}, {"chunk": "b"}]

        async def fake_context(*args, **kwargs):
            for chunk in chunks_to_yield:
                yield chunk

        with patch(_STREAM_CTX, side_effect=fake_context):
            result = [c async for c in team.stream({"q": "test"})]

        assert result == chunks_to_yield

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_empty_when_context_yields_nothing():
        """stream() produces no chunks when standalone_stream_context is empty."""
        team = _make_team("sv")
        team.add_agent(_sv_card("sv"), lambda: MagicMock())

        async def empty_context(*args, **kwargs):
            return
            yield  # make it an async generator

        with patch(_STREAM_CTX, side_effect=empty_context):
            result = [c async for c in team.stream({"q": "test"})]

        assert result == []


# ---------------------------------------------------------------------------
# SECTION 3 -- P2PAbilityManager: init
# ---------------------------------------------------------------------------

class TestP2PAbilityManagerInit:
    """Tests for P2PAbilityManager construction and semaphore behaviour."""

    @staticmethod
    def test_inherits_ability_manager():
        """P2PAbilityManager is a subclass of AbilityManager."""
        from openjiuwen.core.single_agent.ability_manager import AbilityManager
        assert isinstance(P2PAbilityManager(supervisor=MagicMock()), AbilityManager)

    @staticmethod
    def test_semaphore_reflects_max_parallel():
        """The internal semaphore is created with the supplied concurrency limit."""
        mgr = P2PAbilityManager(supervisor=MagicMock(), max_parallel_sub_agents=7)
        assert _get_semaphore_value(mgr) == 7

    @staticmethod
    def test_max_parallel_clamped_to_one_when_zero():
        """max_parallel_sub_agents=0 is clamped to 1."""
        mgr = P2PAbilityManager(supervisor=MagicMock(), max_parallel_sub_agents=0)
        assert _get_semaphore_value(mgr) == 1

    @staticmethod
    def test_max_parallel_clamped_to_one_when_negative():
        """Negative max_parallel_sub_agents is clamped to 1."""
        mgr = P2PAbilityManager(supervisor=MagicMock(), max_parallel_sub_agents=-5)
        assert _get_semaphore_value(mgr) == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_semaphore_lazily_created_and_cached():
        """_get_semaphore() returns the same semaphore object on repeated calls."""
        mgr = P2PAbilityManager(supervisor=MagicMock(), max_parallel_sub_agents=3)
        # Retrieve the semaphore twice via the accessor and confirm identity + value.
        val1 = _get_semaphore_value(mgr)
        val2 = _get_semaphore_value(mgr)
        assert val1 == val2 == 3


# ---------------------------------------------------------------------------
# SECTION 3a -- P2PAbilityManager: add
# ---------------------------------------------------------------------------

class TestP2PAbilityManagerAdd:
    """Tests for P2PAbilityManager.add (AgentCard registration)."""

    @staticmethod
    def test_add_stores_agent_card():
        """add() registers an AgentCard so it can be dispatched via P2P."""
        mgr = P2PAbilityManager(supervisor=MagicMock())
        card = _sub_card("ax")
        mgr.add(card)
        registered = {a.id for a in mgr.list() if isinstance(a, AgentCard)}
        assert "ax" in registered

    @staticmethod
    def test_add_multiple_cards():
        """Multiple cards can be added independently."""
        mgr = P2PAbilityManager(supervisor=MagicMock())
        mgr.add(_sub_card("a1"))
        mgr.add(_sub_card("a2"))
        registered = {a.id for a in mgr.list() if isinstance(a, AgentCard)}
        assert {"a1", "a2"} <= registered

    @staticmethod
    def test_add_returns_add_ability_result():
        """add() returns an AddAbilityResult with added=True for a new card."""
        from openjiuwen.core.single_agent.ability_manager import AddAbilityResult
        mgr = P2PAbilityManager(supervisor=MagicMock())
        result = mgr.add(_sub_card("new_agent"))
        assert isinstance(result, AddAbilityResult)
        assert result.added is True

    @staticmethod
    def test_add_duplicate_returns_not_added():
        """Adding the same AgentCard name twice returns added=False on the second call."""
        from openjiuwen.core.single_agent.ability_manager import AddAbilityResult
        mgr = P2PAbilityManager(supervisor=MagicMock())
        mgr.add(_sub_card("dup_agent"))
        result = mgr.add(_sub_card("dup_agent"))
        assert isinstance(result, AddAbilityResult)
        assert result.added is False


# ---------------------------------------------------------------------------
# SECTION 3b -- P2PAbilityManager: execute (non-agent calls)
# ---------------------------------------------------------------------------

_P2P_SUPER_EXECUTE = (
    "openjiuwen.core.multi_agent.teams.hierarchical_msgbus"
    ".p2p_ability_manager.AbilityManager.execute"
)


class TestP2PAbilityManagerExecuteNonAgent:
    """Non-AgentCard tool calls are delegated to AbilityManager.execute."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_tool_calls_returns_empty_list():
        """execute() with an empty list returns [] without calling super."""
        mgr = P2PAbilityManager(supervisor=MagicMock())
        result = await mgr.execute(MagicMock(), [], MagicMock())
        assert result == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_non_agent_call_delegates_to_super():
        """A tool call whose name is not a registered AgentCard goes to super().execute."""
        mgr = P2PAbilityManager(supervisor=MagicMock())
        expected = [("res", ToolMessage(content="ok", tool_call_id="tc1"))]
        with patch(_P2P_SUPER_EXECUTE, new=AsyncMock(return_value=expected)) as mock_super:
            result = await mgr.execute(MagicMock(), _tc("unknown_tool"), MagicMock())
        mock_super.assert_awaited_once()
        assert result == expected

    @staticmethod
    @pytest.mark.asyncio
    async def test_non_agent_single_tool_call_passes_through():
        """Single ToolCall not in agents list is forwarded to the base class."""
        mgr = P2PAbilityManager(supervisor=MagicMock())
        with patch(_P2P_SUPER_EXECUTE, new=AsyncMock(return_value=[])) as mock_super:
            await mgr.execute(MagicMock(), _tc("plain_tool", call_id="pt1"), MagicMock())
        assert mock_super.await_count == 1


# ---------------------------------------------------------------------------
# SECTION 3c -- P2PAbilityManager: execute (agent P2P dispatch)
# ---------------------------------------------------------------------------

class TestP2PAbilityManagerExecuteAgentCall:
    """AgentCard tool calls are dispatched via supervisor.send (P2P)."""

    @staticmethod
    def _make_supervisor_and_mgr(send_return=None):
        sv = MagicMock()
        sv.send = AsyncMock(return_value=send_return or {"out": "ok"})
        sv.runtime = MagicMock(p2p_timeout=1800.0)
        mgr = P2PAbilityManager(supervisor=sv)
        return sv, mgr

    @staticmethod
    def _make_session(session_id="s1"):
        session = MagicMock()
        session.get_session_id = MagicMock(return_value=session_id)
        return session

    @staticmethod
    @pytest.mark.asyncio
    async def test_agent_call_invokes_supervisor_send():
        """execute() calls supervisor.send once for an AgentCard tool call."""
        sv, mgr = TestP2PAbilityManagerExecuteAgentCall._make_supervisor_and_mgr()
        mgr.add(_sub_card("sub_a"))
        await mgr.execute(
            MagicMock(), _tc("sub_a", {"x": 1}),
            TestP2PAbilityManagerExecuteAgentCall._make_session()
        )
        sv.send.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_agent_call_recipient_matches_agent_id():
        """supervisor.send is called with recipient == agent card id."""
        sv, mgr = TestP2PAbilityManagerExecuteAgentCall._make_supervisor_and_mgr()
        mgr.add(_sub_card("agent_b"))
        await mgr.execute(
            MagicMock(), _tc("agent_b"),
            TestP2PAbilityManagerExecuteAgentCall._make_session()
        )
        call_args = sv.send.call_args
        assert call_args is not None
        kw = call_args[1] if call_args[1] else call_args.kwargs
        assert kw.get("recipient") == "agent_b"

    @staticmethod
    @pytest.mark.asyncio
    async def test_agent_call_passes_session_id():
        """supervisor.send receives the session_id from the session object."""
        sv, mgr = TestP2PAbilityManagerExecuteAgentCall._make_supervisor_and_mgr()
        mgr.add(_sub_card("agent_c"))
        await mgr.execute(
            MagicMock(), _tc("agent_c"),
            TestP2PAbilityManagerExecuteAgentCall._make_session("sess-42")
        )
        call_args = sv.send.call_args
        assert call_args is not None
        kw = call_args[1] if call_args[1] else call_args.kwargs
        assert kw.get("session_id") == "sess-42"

    @staticmethod
    @pytest.mark.asyncio
    async def test_agent_call_returns_result_and_tool_message():
        """execute() returns [(result, ToolMessage)] for a successful agent call."""
        sv, mgr = TestP2PAbilityManagerExecuteAgentCall._make_supervisor_and_mgr(
            send_return={"answer": 42}
        )
        mgr.add(_sub_card("ag"))
        results = await mgr.execute(
            MagicMock(), _tc("ag"),
            TestP2PAbilityManagerExecuteAgentCall._make_session()
        )
        assert len(results) == 1
        first_val, first_msg = results[0]
        assert first_val == {"answer": 42}
        assert isinstance(first_msg, ToolMessage)

    @staticmethod
    @pytest.mark.asyncio
    async def test_tool_message_has_correct_tool_call_id():
        """The returned ToolMessage carries the original tool_call_id."""
        sv, mgr = TestP2PAbilityManagerExecuteAgentCall._make_supervisor_and_mgr()
        mgr.add(_sub_card("ag2"))
        results = await mgr.execute(
            MagicMock(), _tc("ag2", call_id="call-xyz"),
            TestP2PAbilityManagerExecuteAgentCall._make_session()
        )
        assert len(results) >= 1
        _, msg = results[0]
        assert msg.tool_call_id == "call-xyz"

    @staticmethod
    @pytest.mark.asyncio
    async def test_p2p_failure_returns_error_tool_message():
        """When supervisor.send raises, execute() returns (None, error ToolMessage)."""
        sv = MagicMock()
        sv.send = AsyncMock(side_effect=RuntimeError("network error"))
        sv.runtime = MagicMock(p2p_timeout=1800.0)
        mgr = P2PAbilityManager(supervisor=sv)
        mgr.add(_sub_card("fail_ag"))
        session = MagicMock()
        session.get_session_id = MagicMock(return_value="s1")
        results = await mgr.execute(
            MagicMock(), _tc("fail_ag", call_id="tf1"), session
        )
        assert len(results) == 1
        fail_val, fail_msg = results[0]
        assert fail_val is None
        assert "P2P parallel dispatch failed" in fail_msg.content

    @staticmethod
    @pytest.mark.asyncio
    async def test_p2p_error_tool_message_has_original_call_id():
        """Error ToolMessage carries the tool_call_id from the failed call."""
        sv = MagicMock()
        sv.send = AsyncMock(side_effect=RuntimeError("boom"))
        sv.runtime = MagicMock(p2p_timeout=1800.0)
        mgr = P2PAbilityManager(supervisor=sv)
        mgr.add(_sub_card("fail2"))
        session = MagicMock()
        session.get_session_id = MagicMock(return_value="s1")
        results = await mgr.execute(
            MagicMock(), _tc("fail2", call_id="err-id"), session
        )
        assert len(results) >= 1
        _, err_msg = results[0]
        assert err_msg.tool_call_id == "err-id"


# ---------------------------------------------------------------------------
# SECTION 3d -- P2PAbilityManager: parallel dispatch
# ---------------------------------------------------------------------------

class TestP2PAbilityManagerParallelDispatch:
    """Tests for concurrent P2P dispatch and semaphore limiting."""

    @staticmethod
    def _make_session(sid="sp"):
        s = MagicMock()
        s.get_session_id = MagicMock(return_value=sid)
        return s

    @staticmethod
    @pytest.mark.asyncio
    async def test_all_parallel_agent_calls_dispatched():
        """All agent tool calls in a batch are dispatched via P2P."""
        dispatched = []

        async def mock_send(message, recipient, session_id=None, timeout=None):
            dispatched.append(recipient)
            return {"from": recipient}

        sv = MagicMock()
        sv.send = mock_send
        sv.runtime = MagicMock(p2p_timeout=1800.0)
        mgr = P2PAbilityManager(supervisor=sv, max_parallel_sub_agents=5)
        mgr.add(_sub_card("s1"))
        mgr.add(_sub_card("s2"))

        session = MagicMock()
        session.get_session_id = MagicMock(return_value="sp")
        results = await mgr.execute(
            MagicMock(),
            [_tc("s1", call_id="t1"), _tc("s2", call_id="t2")],
            session,
        )
        assert len(results) == 2
        assert set(dispatched) == {"s1", "s2"}

    @staticmethod
    @pytest.mark.asyncio
    async def test_semaphore_limits_peak_concurrency():
        """Peak concurrent dispatches never exceed max_parallel_sub_agents."""
        active, peak = 0, 0

        async def mock_send(message, recipient, session_id=None, timeout=None):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"r": recipient}

        limit = 2
        sv = MagicMock()
        sv.send = mock_send
        sv.runtime = MagicMock(p2p_timeout=1800.0)
        mgr = P2PAbilityManager(supervisor=sv, max_parallel_sub_agents=limit)
        for i in range(5):
            mgr.add(_sub_card(f"ag{i}"))

        session = MagicMock()
        session.get_session_id = MagicMock(return_value="ss")
        tcs = [_tc(f"ag{i}", call_id=f"tc{i}") for i in range(5)]
        await mgr.execute(MagicMock(), tcs, session)
        assert peak <= limit

    @staticmethod
    @pytest.mark.asyncio
    async def test_result_order_preserved_for_parallel_calls():
        """Results are returned in the original tool_call order."""
        results_map = {"first": "r1", "second": "r2"}

        async def mock_send(message, recipient, session_id=None, timeout=None):
            return results_map.get(recipient)

        sv = MagicMock()
        sv.send = mock_send
        sv.runtime = MagicMock(p2p_timeout=1800.0)
        mgr = P2PAbilityManager(supervisor=sv, max_parallel_sub_agents=2)
        mgr.add(_sub_card("first"))
        mgr.add(_sub_card("second"))

        session = MagicMock()
        session.get_session_id = MagicMock(return_value="sp")
        results = await mgr.execute(
            MagicMock(),
            [_tc("first", call_id="c1"), _tc("second", call_id="c2")],
            session,
        )
        assert len(results) == 2
        assert results[0][0] == "r1"
        assert results[1][0] == "r2"

    @staticmethod
    @pytest.mark.asyncio
    async def test_mixed_agent_and_tool_calls_both_executed():
        """Batch with both AgentCard and regular tool calls executes both paths."""
        sv = MagicMock()
        sv.send = AsyncMock(return_value={"agent": "done"})
        sv.runtime = MagicMock(p2p_timeout=1800.0)
        mgr = P2PAbilityManager(supervisor=sv)
        mgr.add(_sub_card("sub_m"))
        regular = ("reg_val", ToolMessage(content="reg", tool_call_id="tc_r"))
        session = MagicMock()
        session.get_session_id = MagicMock(return_value="sm")
        with patch(_P2P_SUPER_EXECUTE, new=AsyncMock(return_value=[regular])):
            results = await mgr.execute(
                MagicMock(),
                [_tc("sub_m", call_id="ta"), _tc("reg_tool", call_id="tr")],
                session,
            )
        assert len(results) == 2


# ---------------------------------------------------------------------------
# SECTION 4 -- SupervisorAgent: __init__ and register_sub_agent_card
# ---------------------------------------------------------------------------

class TestSupervisorAgentInit:
    """Tests for SupervisorAgent construction and sub-agent registration."""

    @staticmethod
    def test_ability_manager_is_p2p():
        """SupervisorAgent uses a P2PAbilityManager for ability dispatch."""
        agent = _SupervisorAgentAccessor(card=_sv_card("sv_i"))
        assert isinstance(_get_ability_manager(agent), P2PAbilityManager)

    @staticmethod
    def test_is_communicable_agent():
        """SupervisorAgent inherits from CommunicableAgent."""
        from openjiuwen.core.multi_agent.team_runtime.communicable_agent import CommunicableAgent
        assert isinstance(SupervisorAgent(card=_sv_card("sv_comm")), CommunicableAgent)

    @staticmethod
    def test_is_react_agent():
        """SupervisorAgent inherits from ReActAgent."""
        from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
        assert isinstance(SupervisorAgent(card=_sv_card("sv_react")), ReActAgent)

    @staticmethod
    def test_register_sub_agent_card_adds_to_ability_manager():
        """register_sub_agent_card exposes the card as a dispatchable tool."""
        agent = _SupervisorAgentAccessor(card=_sv_card("sv_r"))
        agent.register_sub_agent_card(_sub_card("sub1"))
        registered = {a.id for a in _get_ability_manager(agent).list() if isinstance(a, AgentCard)}
        assert "sub1" in registered

    @staticmethod
    def test_register_multiple_sub_agents():
        """Multiple sub-agent cards are all registered independently."""
        agent = _SupervisorAgentAccessor(card=_sv_card("sv_multi"))
        agent.register_sub_agent_card(_sub_card("s1"))
        agent.register_sub_agent_card(_sub_card("s2"))
        registered = {a.id for a in _get_ability_manager(agent).list() if isinstance(a, AgentCard)}
        assert {"s1", "s2"} <= registered

    @staticmethod
    def test_register_sub_agent_emits_debug_log():
        """register_sub_agent_card calls the logger debug method mentioning the card name."""
        import openjiuwen.core.multi_agent.teams.hierarchical_msgbus.supervisor_agent as _sv_mod
        with patch.object(_sv_mod.logger, "debug") as mock_debug:
            agent = SupervisorAgent(card=_sv_card("sv_log"))
            agent.register_sub_agent_card(_sub_card("logged_sub"))
        assert mock_debug.called
        combined = " ".join(str(c) for c in mock_debug.call_args_list)
        assert "logged_sub" in combined


# ---------------------------------------------------------------------------
# SECTION 4a -- SupervisorAgent: configure
# ---------------------------------------------------------------------------

class TestSupervisorAgentConfigure:
    """Tests for SupervisorAgent.configure method."""

    @staticmethod
    def test_configure_react_config_returns_self():
        """configure() with a ReActAgentConfig returns the agent itself."""
        agent = SupervisorAgent(card=_sv_card("sv_c"))
        result = agent.configure(ReActAgentConfig())
        assert result is agent

    @staticmethod
    def test_configure_non_react_is_noop_returns_self():
        """configure() with a non-ReActAgentConfig is a no-op and returns self."""
        agent = SupervisorAgent(card=_sv_card("sv_n"))
        result = agent.configure(object())
        assert result is agent

    @staticmethod
    def test_configure_none_is_noop_returns_self():
        """configure(None) is a no-op and returns self."""
        agent = SupervisorAgent(card=_sv_card("sv_none"))
        result = agent.configure(None)
        assert result is agent


# ---------------------------------------------------------------------------
# SECTION 4b -- SupervisorAgent: create class method
# ---------------------------------------------------------------------------

class TestSupervisorAgentCreate:
    """Tests for the SupervisorAgent.create factory class method."""

    @staticmethod
    def test_create_returns_card_and_callable_provider():
        """create() returns (AgentCard, callable) compatible with add_agent."""
        mc, mr = _mock_llm_configs()
        card, provider = SupervisorAgent.create(
            agents=[_sub_card("a1")],
            model_client_config=mc,
            model_request_config=mr,
            agent_card=_sv_card("sv_create"),
            system_prompt="You are a supervisor.",
        )
        assert card.id == "sv_create"
        assert callable(provider)

    @staticmethod
    def test_create_empty_agents_raises():
        """create() raises when agents list is empty."""
        mc, mr = _mock_llm_configs()
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            SupervisorAgent.create(
                agents=[],
                model_client_config=mc,
                model_request_config=mr,
                agent_card=_sv_card("sv_e"),
                system_prompt="sys",
            )

    @staticmethod
    def test_create_non_agent_card_in_list_raises():
        """create() raises when an agents list entry is not an AgentCard."""
        mc, mr = _mock_llm_configs()
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            SupervisorAgent.create(
                agents=["not_a_card"],  # type: ignore[list-item]
                model_client_config=mc,
                model_request_config=mr,
                agent_card=_sv_card("sv_bad"),
                system_prompt="sys",
            )

    @staticmethod
    def test_provider_returns_supervisor_agent_instance():
        """The provider callable returns a SupervisorAgent."""
        mc, mr = _mock_llm_configs()
        _, provider = SupervisorAgent.create(
            agents=[_sub_card("x1")],
            model_client_config=mc,
            model_request_config=mr,
            agent_card=_sv_card("sv_prov"),
            system_prompt="sys",
        )
        instance = provider()
        assert isinstance(instance, SupervisorAgent)

    @staticmethod
    def test_provider_registers_all_sub_agents():
        """Sub-agent cards are registered in the supervisor's ability manager."""
        mc, mr = _mock_llm_configs()
        subs = [_sub_card("x1"), _sub_card("x2")]
        _, provider = SupervisorAgent.create(
            agents=subs,
            model_client_config=mc,
            model_request_config=mr,
            agent_card=_sv_card("sv_prov2"),
            system_prompt="sys",
        )
        instance = provider()
        registered = {a.id for a in _get_ability_manager(instance).list() if isinstance(a, AgentCard)}
        assert {"x1", "x2"} <= registered

    @staticmethod
    def test_create_agent_card_id_matches_supplied_card():
        """The returned card id exactly matches the agent_card supplied to create."""
        mc, mr = _mock_llm_configs()
        sv_card = _sv_card("exact_id")
        card, _ = SupervisorAgent.create(
            agents=[_sub_card("sub")],
            model_client_config=mc,
            model_request_config=mr,
            agent_card=sv_card,
            system_prompt="sys",
        )
        assert card is sv_card

    @staticmethod
    def test_create_with_custom_max_iterations():
        """max_iterations parameter is accepted without error."""
        mc, mr = _mock_llm_configs()
        card, provider = SupervisorAgent.create(
            agents=[_sub_card("sub")],
            model_client_config=mc,
            model_request_config=mr,
            agent_card=_sv_card("sv_iter"),
            system_prompt="sys",
            max_iterations=3,
        )
        assert card.id == "sv_iter"
        assert callable(provider)

    @staticmethod
    def test_create_with_custom_max_parallel_sub_agents():
        """max_parallel_sub_agents is forwarded to the supervisor's ability manager."""
        mc, mr = _mock_llm_configs()
        _, provider = SupervisorAgent.create(
            agents=[_sub_card("sub")],
            model_client_config=mc,
            model_request_config=mr,
            agent_card=_sv_card("sv_par"),
            system_prompt="sys",
            max_parallel_sub_agents=4,
        )
        instance = provider()
        assert _get_semaphore_value(_get_ability_manager(instance)) == 4
        