# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for HandoffOrchestrator.

Coverage:
1. build_route_graph  -- full-mesh, explicit routes, empty agents
2. __init__           -- defaults from config, no config
3. request_handoff    -- approve/reject routes, max_handoffs, termination_condition
4. complete / error   -- resolves/rejects done_future, idempotency
5. done_future        -- lazy creation, caching
6. properties         -- handoff_count, current_agent_id
7. save_to_session / restore_from_session -- snapshot round-trip
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.multi_agent.teams.handoff.handoff_config import HandoffConfig, HandoffRoute
from openjiuwen.core.multi_agent.teams.handoff.handoff_orchestrator import (
    COORDINATOR_STATE_KEY,
    HandoffOrchestrator,
)


def _make_coord(
    start: str = "a",
    agents=None,
    routes=None,
    max_handoffs: int = 10,
    termination_condition=None,
) -> HandoffOrchestrator:
    if agents is None:
        agents = ["a", "b", "c"]
    cfg = HandoffConfig(
        max_handoffs=max_handoffs,
        routes=routes or [],
        termination_condition=termination_condition,
    )
    return HandoffOrchestrator(
        start_agent_id=start,
        registered_agents=agents,
        config=cfg,
    )


def _make_session(snapshot=None):
    session = MagicMock()
    session.update_state = MagicMock()
    session.get_state = MagicMock(return_value=snapshot)
    return session


class TestBuildRouteGraph:
    @staticmethod
    def test_full_mesh_no_routes():
        graph = HandoffOrchestrator.build_route_graph(["a", "b", "c"], [])
        assert graph["a"] == {"b", "c"}
        assert graph["b"] == {"a", "c"}
        assert graph["c"] == {"a", "b"}

    @staticmethod
    def test_full_mesh_no_self_loops():
        graph = HandoffOrchestrator.build_route_graph(["a", "b"], [])
        assert "a" not in graph["a"]
        assert "b" not in graph["b"]

    @staticmethod
    def test_explicit_routes_respected():
        routes = [HandoffRoute("a", "b"), HandoffRoute("b", "c")]
        graph = HandoffOrchestrator.build_route_graph(["a", "b", "c"], routes)
        assert graph["a"] == {"b"}
        assert graph["b"] == {"c"}
        assert graph["c"] == set()

    @staticmethod
    def test_explicit_routes_non_source_has_empty_set():
        routes = [HandoffRoute("a", "b")]
        graph = HandoffOrchestrator.build_route_graph(["a", "b", "c"], routes)
        assert graph["b"] == set()
        assert graph["c"] == set()

    @staticmethod
    def test_single_agent_empty_targets():
        graph = HandoffOrchestrator.build_route_graph(["a"], [])
        assert graph["a"] == set()

    @staticmethod
    def test_empty_agents_empty_graph():
        assert HandoffOrchestrator.build_route_graph([], []) == {}

    @staticmethod
    def test_multiple_routes_from_same_source():
        routes = [HandoffRoute("a", "b"), HandoffRoute("a", "c")]
        graph = HandoffOrchestrator.build_route_graph(["a", "b", "c"], routes)
        assert graph["a"] == {"b", "c"}

    @staticmethod
    def test_is_static_method():
        assert isinstance(
            HandoffOrchestrator.__dict__["build_route_graph"], staticmethod
        )


class TestHandoffOrchestratorInit:
    @staticmethod
    def test_initial_handoff_count_zero():
        assert _make_coord().handoff_count == 0

    @staticmethod
    def test_initial_current_agent_id():
        assert _make_coord(start="a").current_agent_id == "a"

    @staticmethod
    def test_no_config_uses_default_max_handoffs():
        coord = HandoffOrchestrator(start_agent_id="a", registered_agents=["a", "b"])
        assert getattr(coord, "_max_handoffs") == 10

    @staticmethod
    def test_config_max_handoffs_applied():
        assert getattr(_make_coord(max_handoffs=3), "_max_handoffs") == 3

    @staticmethod
    def test_done_future_initially_none():
        assert getattr(_make_coord(), "_done_future") is None


class TestRequestHandoff:
    @pytest.mark.asyncio
    async def test_approves_valid_full_mesh_route(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        assert await coord.request_handoff("b") is True

    @pytest.mark.asyncio
    async def test_increments_handoff_count_on_approval(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        await coord.request_handoff("b")
        assert coord.handoff_count == 1

    @pytest.mark.asyncio
    async def test_updates_current_agent_id_on_approval(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        await coord.request_handoff("b")
        assert coord.current_agent_id == "b"

    @pytest.mark.asyncio
    async def test_rejects_when_max_handoffs_zero(self):
        coord = _make_coord(start="a", agents=["a", "b"], max_handoffs=0)
        assert await coord.request_handoff("b") is False

    @pytest.mark.asyncio
    async def test_rejects_when_max_handoffs_reached(self):
        coord = _make_coord(start="a", agents=["a", "b", "c"], max_handoffs=1)
        await coord.request_handoff("b")
        assert await coord.request_handoff("c") is False

    @pytest.mark.asyncio
    async def test_no_count_increment_on_rejection(self):
        coord = _make_coord(start="a", agents=["a", "b"], max_handoffs=0)
        await coord.request_handoff("b")
        assert coord.handoff_count == 0

    @pytest.mark.asyncio
    async def test_no_agent_id_change_on_rejection(self):
        coord = _make_coord(start="a", agents=["a", "b"], max_handoffs=0)
        await coord.request_handoff("b")
        assert coord.current_agent_id == "a"

    @pytest.mark.asyncio
    async def test_rejects_invalid_explicit_route(self):
        routes = [HandoffRoute("a", "b")]
        coord = _make_coord(start="a", agents=["a", "b", "c"], routes=routes)
        assert await coord.request_handoff("c") is False

    @pytest.mark.asyncio
    async def test_approves_valid_explicit_route(self):
        routes = [HandoffRoute("a", "b")]
        coord = _make_coord(start="a", agents=["a", "b"], routes=routes)
        assert await coord.request_handoff("b") is True

    @pytest.mark.asyncio
    async def test_rejects_when_sync_termination_true(self):
        coord = _make_coord(start="a", agents=["a", "b"], termination_condition=lambda c: True)
        assert await coord.request_handoff("b") is False

    @pytest.mark.asyncio
    async def test_approves_when_sync_termination_false(self):
        coord = _make_coord(start="a", agents=["a", "b"], termination_condition=lambda c: False)
        assert await coord.request_handoff("b") is True

    @pytest.mark.asyncio
    async def test_rejects_when_async_termination_true(self):
        async def always_stop(c):
            return True
        coord = _make_coord(start="a", agents=["a", "b"], termination_condition=always_stop)
        assert await coord.request_handoff("b") is False

    @pytest.mark.asyncio
    async def test_approves_when_async_termination_false(self):
        async def never_stop(c):
            return False
        coord = _make_coord(start="a", agents=["a", "b"], termination_condition=never_stop)
        assert await coord.request_handoff("b") is True

    @pytest.mark.asyncio
    async def test_chained_handoffs_track_count(self):
        coord = _make_coord(start="a", agents=["a", "b", "c"], max_handoffs=5)
        await coord.request_handoff("b")
        await coord.request_handoff("c")
        assert coord.handoff_count == 2

    @pytest.mark.asyncio
    async def test_chained_handoffs_track_current_agent(self):
        coord = _make_coord(start="a", agents=["a", "b", "c"], max_handoffs=5)
        await coord.request_handoff("b")
        await coord.request_handoff("c")
        assert coord.current_agent_id == "c"


class TestCompleteAndError:
    @pytest.mark.asyncio
    async def test_complete_resolves_future(self):
        coord = _make_coord()
        await coord.complete({"answer": 42})
        assert coord.done_future.result() == {"answer": 42}

    @pytest.mark.asyncio
    async def test_complete_with_none_result(self):
        coord = _make_coord()
        await coord.complete(None)
        assert coord.done_future.result() is None

    @pytest.mark.asyncio
    async def test_complete_idempotent_first_wins(self):
        coord = _make_coord()
        await coord.complete("first")
        await coord.complete("second")
        assert coord.done_future.result() == "first"

    @pytest.mark.asyncio
    async def test_error_rejects_future(self):
        coord = _make_coord()
        await coord.error(ValueError("boom"))
        with pytest.raises(ValueError, match="boom"):
            coord.done_future.result()

    @pytest.mark.asyncio
    async def test_error_idempotent_first_exception_wins(self):
        coord = _make_coord()
        await coord.error(ValueError("first"))
        await coord.error(RuntimeError("second"))
        with pytest.raises(ValueError, match="first"):
            coord.done_future.result()

    @pytest.mark.asyncio
    async def test_done_future_done_after_complete(self):
        coord = _make_coord()
        await coord.complete("ok")
        assert coord.done_future.done() is True

    @pytest.mark.asyncio
    async def test_done_future_done_after_error(self):
        coord = _make_coord()
        await coord.error(RuntimeError("err"))
        assert coord.done_future.done() is True


class TestDoneFuture:
    @pytest.mark.asyncio
    async def test_done_future_is_asyncio_future(self):
        coord = _make_coord()
        assert isinstance(coord.done_future, asyncio.Future)

    @pytest.mark.asyncio
    async def test_done_future_cached(self):
        coord = _make_coord()
        assert coord.done_future is coord.done_future

    @pytest.mark.asyncio
    async def test_done_future_not_done_initially(self):
        coord = _make_coord()
        assert coord.done_future.done() is False


class TestProperties:
    @staticmethod
    def test_handoff_count_zero_initially():
        assert _make_coord().handoff_count == 0

    @staticmethod
    def test_current_agent_id_reflects_start():
        assert _make_coord(start="x", agents=["x", "y"]).current_agent_id == "x"

    @pytest.mark.asyncio
    async def test_handoff_count_increments(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        await coord.request_handoff("b")
        assert coord.handoff_count == 1

    @pytest.mark.asyncio
    async def test_current_agent_id_updates(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        await coord.request_handoff("b")
        assert coord.current_agent_id == "b"


class TestSaveRestoreSession:
    @pytest.mark.asyncio
    async def test_save_calls_update_state(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        await coord.request_handoff("b")
        session = _make_session()
        coord.save_to_session(session)
        session.update_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_persists_current_agent(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        await coord.request_handoff("b")
        session = _make_session()
        coord.save_to_session(session)
        saved = session.update_state.call_args[0][0][COORDINATOR_STATE_KEY]
        assert saved["current_agent_id"] == "b"

    @pytest.mark.asyncio
    async def test_save_persists_handoff_count(self):
        coord = _make_coord(start="a", agents=["a", "b"])
        await coord.request_handoff("b")
        session = _make_session()
        coord.save_to_session(session)
        saved = session.update_state.call_args[0][0][COORDINATOR_STATE_KEY]
        assert saved["handoff_count"] == 1

    @staticmethod
    def test_restore_with_snapshot():
        snapshot = {"current_agent_id": "b", "handoff_count": 3}
        coord = HandoffOrchestrator.restore_from_session(
            session=_make_session(snapshot=snapshot),
            start_agent_id="a",
            registered_agents=["a", "b"],
        )
        assert coord.current_agent_id == "b"
        assert coord.handoff_count == 3

    @staticmethod
    def test_restore_without_snapshot_uses_start():
        coord = HandoffOrchestrator.restore_from_session(
            session=_make_session(snapshot=None),
            start_agent_id="a",
            registered_agents=["a", "b"],
        )
        assert coord.current_agent_id == "a"
        assert coord.handoff_count == 0

    @staticmethod
    def test_restore_is_classmethod():
        assert isinstance(
            HandoffOrchestrator.__dict__["restore_from_session"], classmethod
        )

    @staticmethod
    def test_restore_returns_orchestrator_instance():
        coord = HandoffOrchestrator.restore_from_session(
            session=_make_session(snapshot=None),
            start_agent_id="a",
            registered_agents=["a", "b"],
        )
        assert isinstance(coord, HandoffOrchestrator)

    @pytest.mark.asyncio
    async def test_save_restore_round_trip(self):
        coord = _make_coord(start="a", agents=["a", "b", "c"], max_handoffs=5)
        await coord.request_handoff("b")
        await coord.request_handoff("c")
        saved_state = {}
        session = _make_session()

        def _update_state(d):
            saved_state.update(d)

        def _get_state(key):
            return saved_state.get(key)

        session.update_state = _update_state
        session.get_state = _get_state
        coord.save_to_session(session)
        restored = HandoffOrchestrator.restore_from_session(
            session=session,
            start_agent_id="a",
            registered_agents=["a", "b", "c"],
        )
        assert restored.current_agent_id == "c"
        assert restored.handoff_count == 2
 