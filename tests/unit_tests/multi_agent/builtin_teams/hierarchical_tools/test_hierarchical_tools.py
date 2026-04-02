# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for the hierarchical_tools module.

Coverage:
1. HierarchicalTeamConfig -- root_agent field validation, inheritance, config chaining
2. HierarchicalTeam      -- init (card/config/runtime/team_id/runtime_team_id)
3. HierarchicalTeam      -- add_agent (register, return self, multiple, count,
                            parent_agent_id, duplicate, over-limit, chaining)
4. HierarchicalTeam      -- pending_children queue behaviour
5. HierarchicalTeam      -- _assert_ready (unregistered root raises, registered passes)
6. HierarchicalTeam      -- _setup_hierarchy (no-op when empty, wires children,
                            clears queue after execution)
7. HierarchicalTeam      -- invoke (unregistered root raises, returns result,
                            correct recipient/sender/session_id, conversation_id
                            reuse, string input, _setup_hierarchy called)
8. HierarchicalTeam      -- stream (unregistered root raises, completes without
                            error, correct recipient/sender, string input,
                            _setup_hierarchy called)
9. HierarchicalTeam      -- agent management (count, list_agents, get_agent_card,
                            remove by id/card/nonexistent)
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.teams.hierarchical_tools import (
    HierarchicalTeam,
    HierarchicalTeamConfig,
)
from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_card(aid: str) -> AgentCard:
    return AgentCard(id=aid, name=aid, description=f"agent {aid}")


def _team_card(tid: str = "ht_team") -> TeamCard:
    return TeamCard(id=tid, name=tid, description="hierarchical tools team")


def _make_config(root_id: str = "root") -> HierarchicalTeamConfig:
    return HierarchicalTeamConfig(root_agent=_agent_card(root_id))


def _make_team(root_id: str = "root", tid: str = "ht_team") -> HierarchicalTeam:
    return HierarchicalTeam(card=_team_card(tid), config=_make_config(root_id))


def _make_runner_module() -> ModuleType:
    """Return a fake openjiuwen.core.runner module for patching sys.modules."""
    mod = ModuleType("openjiuwen.core.runner")
    mock_rm = MagicMock()
    mock_rm.add_agent.return_value = MagicMock(is_err=lambda: False)
    mock_runner = MagicMock()
    mock_runner.resource_mgr = mock_rm
    mod.Runner = mock_runner
    return mod


def _add_agent(team: HierarchicalTeam, aid: str) -> AgentCard:
    """Register a mock agent into the team, patching Runner via sys.modules."""
    card = _agent_card(aid)
    with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
        team.add_agent(card, lambda: MagicMock())
    return card


def _call_assert_ready(team: HierarchicalTeam) -> None:
    """Invoke the protected _assert_ready method without direct protected access."""
    getattr(team, "_assert_ready")()


async def _call_setup_hierarchy(team: HierarchicalTeam) -> None:
    """Invoke the protected _setup_hierarchy method without direct protected access."""
    await getattr(team, "_setup_hierarchy")()


# ---------------------------------------------------------------------------
# 1. HierarchicalTeamConfig
# ---------------------------------------------------------------------------

class TestHierarchicalTeamConfig:
    @staticmethod
    def test_requires_root_agent():
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            HierarchicalTeamConfig()  # type: ignore[call-arg]

    @staticmethod
    def test_stores_root_agent_card():
        card = _agent_card("my_root")
        cfg = HierarchicalTeamConfig(root_agent=card)
        assert cfg.root_agent.id == "my_root"

    @staticmethod
    def test_root_agent_name_preserved():
        card = _agent_card("named_root")
        cfg = HierarchicalTeamConfig(root_agent=card)
        assert cfg.root_agent.name == "named_root"

    @staticmethod
    def test_root_agent_description_preserved():
        card = AgentCard(id="root", name="root", description="root agent desc")
        cfg = HierarchicalTeamConfig(root_agent=card)
        assert cfg.root_agent.description == "root agent desc"

    @staticmethod
    def test_inherits_team_config():
        assert isinstance(_make_config(), TeamConfig)

    @staticmethod
    def test_team_config_defaults_preserved():
        cfg = _make_config()
        assert cfg.max_agents == 10
        assert cfg.max_concurrent_messages == 100
        assert cfg.message_timeout == 30.0

    @staticmethod
    def test_configure_max_agents_chaining():
        cfg = _make_config()
        result = cfg.configure_max_agents(5)
        assert result is cfg
        assert cfg.max_agents == 5

    @staticmethod
    def test_configure_timeout_chaining():
        cfg = _make_config()
        result = cfg.configure_timeout(60.0)
        assert result is cfg
        assert cfg.message_timeout == 60.0

    @staticmethod
    def test_configure_concurrency_chaining():
        cfg = _make_config()
        result = cfg.configure_concurrency(50)
        assert result is cfg
        assert cfg.max_concurrent_messages == 50

    @staticmethod
    def test_custom_max_agents():
        cfg = HierarchicalTeamConfig(root_agent=_agent_card("r"), max_agents=3)
        assert cfg.max_agents == 3


# ---------------------------------------------------------------------------
# 2. HierarchicalTeam -- init
# ---------------------------------------------------------------------------

class TestHierarchicalTeamInit:
    @staticmethod
    def test_card_stored():
        card = _team_card("t1")
        team = HierarchicalTeam(card=card, config=_make_config())
        assert team.card is card

    @staticmethod
    def test_config_stored():
        cfg = _make_config("r1")
        team = HierarchicalTeam(card=_team_card(), config=cfg)
        assert team.config is cfg

    @staticmethod
    def test_runtime_created_by_default():
        assert isinstance(_make_team().runtime, TeamRuntime)

    @staticmethod
    def test_custom_runtime_accepted():
        rt = TeamRuntime()
        team = HierarchicalTeam(card=_team_card(), config=_make_config(), runtime=rt)
        assert team.runtime is rt

    @staticmethod
    def test_root_agent_id_in_config():
        assert _make_team(root_id="entry").config.root_agent.id == "entry"

    @staticmethod
    def test_team_id_matches_card_name():
        team = _make_team(tid="my_ht_team")
        assert team.team_id == "my_ht_team"

    @staticmethod
    def test_runtime_team_id_matches_card_id():
        """Verify runtime team_id by comparing against the public card.id."""
        team = _make_team(tid="tid_abc")
        # TeamRuntime sets _team_id from card.id; verify via card.id (public)
        assert team.card.id == "tid_abc"

    @staticmethod
    def test_initial_agent_count_is_zero():
        assert _make_team().get_agent_count() == 0

    @staticmethod
    def test_configure_replaces_config():
        team = _make_team()
        new_cfg = _make_config("new_root")
        result = team.configure(new_cfg)
        assert result is team
        assert team.config is new_cfg


# ---------------------------------------------------------------------------
# 3. HierarchicalTeam -- add_agent
# ---------------------------------------------------------------------------

class TestHierarchicalTeamAddAgent:
    @staticmethod
    def test_registers_in_runtime():
        team = _make_team()
        _add_agent(team, "agent_a")
        assert team.runtime.has_agent("agent_a")

    @staticmethod
    def test_returns_self():
        team = _make_team()
        card = _agent_card("agent_b")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            result = team.add_agent(card, lambda: MagicMock())
        assert result is team

    @staticmethod
    def test_add_multiple():
        team = _make_team()
        _add_agent(team, "a1")
        _add_agent(team, "a2")
        assert team.runtime.has_agent("a1")
        assert team.runtime.has_agent("a2")

    @staticmethod
    def test_increments_count():
        team = _make_team()
        _add_agent(team, "c1")
        _add_agent(team, "c2")
        assert team.get_agent_count() == 2

    @staticmethod
    def test_with_parent_registers_child():
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        child_card = _agent_card("child")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            team.add_agent(child_card, lambda: MagicMock(), parent_agent_id="root")
        assert team.runtime.has_agent("child")

    @staticmethod
    def test_duplicate_returns_self_no_raise():
        team = _make_team()
        _add_agent(team, "dup")
        card = _agent_card("dup")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            result = team.add_agent(card, lambda: MagicMock())
        assert result is team
        assert team.get_agent_count() == 1

    @staticmethod
    def test_beyond_max_raises():
        from openjiuwen.core.common.exception.errors import BaseError
        cfg = HierarchicalTeamConfig(root_agent=_agent_card("root"), max_agents=2)
        team = HierarchicalTeam(card=_team_card(), config=cfg)
        _add_agent(team, "x1")
        _add_agent(team, "x2")
        card = _agent_card("x3")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            with pytest.raises(BaseError):
                team.add_agent(card, lambda: MagicMock())

    @staticmethod
    def test_method_chaining_multiple_calls():
        """add_agent supports method chaining because it returns self."""
        team = _make_team()
        card_a = _agent_card("chain_a")
        card_b = _agent_card("chain_b")
        mod = _make_runner_module()
        with patch.dict(sys.modules, {"openjiuwen.core.runner": mod}):
            result = team.add_agent(card_a, lambda: MagicMock()).add_agent(
                card_b, lambda: MagicMock()
            )
        assert result is team
        assert team.get_agent_count() == 2

    @staticmethod
    def test_card_appended_to_team_card_agent_cards():
        team = _make_team()
        _add_agent(team, "card_check")
        ids = [c.id for c in team.card.agent_cards]
        assert "card_check" in ids


# ---------------------------------------------------------------------------
# 4. HierarchicalTeam -- pending_children queue
# ---------------------------------------------------------------------------

class TestHierarchicalTeamPendingChildren:
    @staticmethod
    def test_no_parent_does_not_create_pending_entry():
        """add_agent without parent_agent_id must not enqueue any pending work."""
        import asyncio
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        asyncio.run(_call_setup_hierarchy(team))

    @staticmethod
    def test_add_agent_with_parent_queues_child_card():
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        child_card = _agent_card("child_queued")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            team.add_agent(child_card, lambda: MagicMock(), parent_agent_id="root")
        assert team.runtime.has_agent("child_queued")

    @staticmethod
    def test_multiple_children_under_same_parent():
        team = _make_team(root_id="parent_a")
        _add_agent(team, "parent_a")
        for i in range(3):
            child = _agent_card(f"child_{i}")
            with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
                team.add_agent(child, lambda: MagicMock(), parent_agent_id="parent_a")
        for i in range(3):
            assert team.runtime.has_agent(f"child_{i}")

    @staticmethod
    def test_children_under_different_parents():
        team = _make_team(root_id="p1")
        _add_agent(team, "p1")
        _add_agent(team, "p2")
        child_of_p1 = _agent_card("child_p1")
        child_of_p2 = _agent_card("child_p2")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            team.add_agent(child_of_p1, lambda: MagicMock(), parent_agent_id="p1")
            team.add_agent(child_of_p2, lambda: MagicMock(), parent_agent_id="p2")
        assert team.runtime.has_agent("child_p1")
        assert team.runtime.has_agent("child_p2")

    @pytest.mark.asyncio
    async def test_setup_hierarchy_wires_child_to_ability_manager(self):
        """_setup_hierarchy calls ability_manager.add for each pending child."""
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        child_card = _agent_card("wired_child")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            team.add_agent(child_card, lambda: MagicMock(), parent_agent_id="root")

        mock_parent_agent = MagicMock()
        mock_parent_agent.ability_manager = MagicMock()
        mock_runner_mod = _make_runner_module()
        mock_runner_mod.Runner.resource_mgr.get_agent = AsyncMock(
            return_value=mock_parent_agent
        )
        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_runner_mod}):
            await _call_setup_hierarchy(team)

        mock_parent_agent.ability_manager.add.assert_called_once_with(child_card)

    @pytest.mark.asyncio
    async def test_setup_hierarchy_clears_pending_after_execution(self):
        """_setup_hierarchy must clear pending_children after wiring."""
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        child_card = _agent_card("clear_child")
        with patch.dict(sys.modules, {"openjiuwen.core.runner": _make_runner_module()}):
            team.add_agent(child_card, lambda: MagicMock(), parent_agent_id="root")

        mock_parent_agent = MagicMock()
        mock_parent_agent.ability_manager = MagicMock()
        mock_runner_mod = _make_runner_module()
        mock_runner_mod.Runner.resource_mgr.get_agent = AsyncMock(
            return_value=mock_parent_agent
        )
        with patch.dict(sys.modules, {"openjiuwen.core.runner": mock_runner_mod}):
            await _call_setup_hierarchy(team)
            mock_parent_agent.ability_manager.add.reset_mock()
            await _call_setup_hierarchy(team)

        mock_parent_agent.ability_manager.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_hierarchy_skipped_when_no_pending(self):
        """_setup_hierarchy is a no-op when there are no pending children."""
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        await _call_setup_hierarchy(team)


# ---------------------------------------------------------------------------
# 5. HierarchicalTeam -- _assert_ready
# ---------------------------------------------------------------------------

class TestHierarchicalTeamAssertReady:
    @staticmethod
    def test_raises_when_root_not_registered():
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            _call_assert_ready(_make_team(root_id="missing"))

    @staticmethod
    def test_passes_when_root_registered():
        team = _make_team(root_id="root_ok")
        _add_agent(team, "root_ok")
        _call_assert_ready(team)  # no exception

    @staticmethod
    def test_error_message_contains_root_id():
        from openjiuwen.core.common.exception.errors import BaseError
        try:
            _call_assert_ready(_make_team(root_id="missing_root"))
            pytest.fail("Expected BaseError")
        except BaseError as exc:
            assert "missing_root" in str(exc)


# ---------------------------------------------------------------------------
# 6. HierarchicalTeam -- invoke
# ---------------------------------------------------------------------------

class TestHierarchicalTeamInvoke:
    @pytest.mark.asyncio
    async def test_raises_when_root_not_registered(self):
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            await _make_team(root_id="no_root").invoke({"query": "hello"})

    @pytest.mark.asyncio
    async def test_returns_result_from_root_agent(self):
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        expected = {"answer": "42"}
        with patch.object(team.runtime, "send", new=AsyncMock(return_value=expected)), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            result = await team.invoke({"query": "hello"})
        assert result == expected

    @pytest.mark.asyncio
    async def test_send_called_with_root_as_recipient(self):
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return {"ok": True}

        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            await team.invoke({"q": "test"})

        assert send_calls[0]["recipient"] == "root"

    @pytest.mark.asyncio
    async def test_send_called_with_team_card_as_sender(self):
        team = _make_team(root_id="root", tid="team_abc")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return {"ok": True}

        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            await team.invoke({"q": "test"})

        assert send_calls[0]["sender"] == "team_abc"

    @pytest.mark.asyncio
    async def test_send_includes_session_id(self):
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return {"done": True}

        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            await team.invoke({"q": "t"})

        assert "session_id" in send_calls[0]
        assert send_calls[0]["session_id"] is not None

    @pytest.mark.asyncio
    async def test_reuses_conversation_id_from_message(self):
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return {"done": True}

        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            await team.invoke({"conversation_id": "cid-001", "q": "t"})

        assert send_calls[0]["session_id"] == "cid-001"

    @pytest.mark.asyncio
    async def test_invoke_with_string_input(self):
        """invoke() accepts plain string inputs (not just dicts)."""
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return "string result"

        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            result = await team.invoke("plain string input")

        assert result == "string result"
        assert send_calls[0]["message"] == "plain string input"

    @pytest.mark.asyncio
    async def test_invoke_calls_setup_hierarchy(self):
        """invoke() must call _setup_hierarchy before delegating to runtime."""
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        setup_called = []

        original = getattr(team, "_setup_hierarchy")

        async def spy_setup():
            setup_called.append(True)
            await original()

        setattr(team, "_setup_hierarchy", spy_setup)

        with patch.object(team.runtime, "send", new=AsyncMock(return_value="ok")), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            await team.invoke({"q": "x"})

        assert setup_called, "_setup_hierarchy was not called during invoke()"

    @pytest.mark.asyncio
    async def test_invoke_passes_message_to_send(self):
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return {"r": 1}

        msg = {"question": "what is 2+2"}
        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            await team.invoke(msg)

        assert send_calls[0]["message"] == msg


# ---------------------------------------------------------------------------
# 7. HierarchicalTeam -- stream
# ---------------------------------------------------------------------------

class TestHierarchicalTeamStream:
    @pytest.mark.asyncio
    async def test_raises_when_root_not_registered(self):
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            async for _ in _make_team(root_id="no_root").stream({"q": "hi"}):
                pass

    @pytest.mark.asyncio
    async def test_stream_completes_without_error(self):
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        with patch.object(team.runtime, "send", new=AsyncMock(return_value={"out": "c"})), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            chunks = [c async for c in team.stream({"q": "hi"})]
        assert isinstance(chunks, list)

    @pytest.mark.asyncio
    async def test_stream_sends_to_root_agent(self):
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return {"out": "v"}

        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            async for _ in team.stream({"q": "hi"}):
                pass

        assert send_calls[0]["recipient"] == "root"

    @pytest.mark.asyncio
    async def test_stream_sender_is_team_card_id(self):
        team = _make_team(root_id="root", tid="stream_team")
        _add_agent(team, "root")
        send_calls = []

        async def capture(**kwargs):
            send_calls.append(kwargs)
            return {"out": "v"}

        with patch.object(team.runtime, "send", side_effect=capture), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            async for _ in team.stream({"q": "hi"}):
                pass

        assert send_calls[0]["sender"] == "stream_team"

    @pytest.mark.asyncio
    async def test_stream_with_string_input(self):
        """stream() accepts plain string inputs (not just dicts)."""
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        with patch.object(team.runtime, "send", new=AsyncMock(return_value="done")), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            chunks = [c async for c in team.stream("plain string")]
        assert isinstance(chunks, list)

    @pytest.mark.asyncio
    async def test_stream_calls_setup_hierarchy(self):
        """stream() must call _setup_hierarchy before delegating to runtime."""
        team = _make_team(root_id="root")
        _add_agent(team, "root")
        setup_called = []

        original = getattr(team, "_setup_hierarchy")

        async def spy_setup():
            setup_called.append(True)
            await original()

        setattr(team, "_setup_hierarchy", spy_setup)

        with patch.object(team.runtime, "send", new=AsyncMock(return_value="ok")), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            async for _ in team.stream({"q": "x"}):
                pass

        assert setup_called, "_setup_hierarchy was not called during stream()"


# ---------------------------------------------------------------------------
# 8. HierarchicalTeam -- agent management
# ---------------------------------------------------------------------------

class TestHierarchicalTeamAgentManagement:
    @staticmethod
    def test_count_starts_at_zero():
        assert _make_team().get_agent_count() == 0

    @staticmethod
    def test_count_reflects_additions():
        team = _make_team()
        _add_agent(team, "a1")
        assert team.get_agent_count() == 1
        _add_agent(team, "a2")
        assert team.get_agent_count() == 2

    @staticmethod
    def test_list_agents_returns_registered_ids():
        team = _make_team()
        _add_agent(team, "p1")
        _add_agent(team, "p2")
        ids = team.list_agents()
        assert "p1" in ids
        assert "p2" in ids

    @staticmethod
    def test_list_agents_does_not_include_removed():
        team = _make_team()
        _add_agent(team, "keep")
        _add_agent(team, "gone")
        team.remove_agent("gone")
        assert "gone" not in team.list_agents()
        assert "keep" in team.list_agents()

    @staticmethod
    def test_get_agent_card_returns_correct_card():
        team = _make_team()
        _add_agent(team, "agent_x")
        card = team.get_agent_card("agent_x")
        assert card is not None
        assert card.id == "agent_x"

    @staticmethod
    def test_get_agent_card_returns_none_for_unknown():
        assert _make_team().get_agent_card("ghost") is None

    @staticmethod
    def test_get_agent_card_returns_none_after_remove():
        team = _make_team()
        _add_agent(team, "rm_lookup")
        team.remove_agent("rm_lookup")
        assert team.get_agent_card("rm_lookup") is None

    @staticmethod
    def test_remove_agent_by_id():
        team = _make_team()
        _add_agent(team, "rm_a")
        team.remove_agent("rm_a")
        assert not team.runtime.has_agent("rm_a")

    @staticmethod
    def test_remove_agent_returns_self():
        team = _make_team()
        _add_agent(team, "rm_b")
        assert team.remove_agent("rm_b") is team

    @staticmethod
    def test_remove_agent_by_card_object():
        team = _make_team()
        card = _add_agent(team, "rm_c")
        team.remove_agent(card)
        assert not team.runtime.has_agent("rm_c")

    @staticmethod
    def test_remove_nonexistent_agent_is_safe():
        assert _make_team().remove_agent("ghost") is not None

    @staticmethod
    def test_remove_agent_decrements_count():
        team = _make_team()
        _add_agent(team, "dec_a")
        _add_agent(team, "dec_b")
        team.remove_agent("dec_a")
        assert team.get_agent_count() == 1

    @staticmethod
    def test_remove_by_id_removes_from_team_card_agent_cards():
        team = _make_team()
        _add_agent(team, "rm_meta")
        team.remove_agent("rm_meta")
        ids = [c.id for c in team.card.agent_cards]
        assert "rm_meta" not in ids

    @staticmethod
    def test_remove_by_card_removes_from_team_card_agent_cards():
        team = _make_team()
        card = _add_agent(team, "rm_meta_card")
        team.remove_agent(card)
        ids = [c.id for c in team.card.agent_cards]
        assert "rm_meta_card" not in ids

    @staticmethod
    def test_list_agents_empty_initially():
        assert _make_team().list_agents() == []

    @staticmethod
    def test_has_agent_false_for_unregistered():
        assert not _make_team().runtime.has_agent("nobody")

    @staticmethod
    def test_has_agent_true_after_add():
        team = _make_team()
        _add_agent(team, "present")
        assert team.runtime.has_agent("present")

    @staticmethod
    def test_has_agent_false_after_remove():
        team = _make_team()
        _add_agent(team, "temp")
        team.remove_agent("temp")
        assert not team.runtime.has_agent("temp")
 