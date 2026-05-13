# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.session.session_controller.data_container import (
    AgentSessionContainer,
    DataContainerFactory,
)
from openjiuwen.core.session.session_controller.scope import (
    MainScope,
    DirectSubject,
    SessionScope,
)
from openjiuwen.core.session.session_controller.session_controller import SessionController


@pytest.fixture
def base_path(tmp_path):
    return tmp_path / "agents"


@pytest.fixture
def controller(base_path):
    ctrl = SessionController(agent_id="agent1", base_path=base_path)
    return ctrl


@pytest.fixture
def main_scope():
    return SessionScope(scope=MainScope())


@pytest.fixture
def direct_scope():
    return SessionScope(scope=MainScope(), subject=DirectSubject("user1"))


class TestSessionControllerInit:
    def test_creates_base_path(self, base_path):
        # SessionController creates the sessions directory on init
        ctrl = SessionController(agent_id="agent1", base_path=base_path)
        assert ctrl.base_path.exists()

    def test_initial_state(self, controller):
        # Fresh controller has no cached sessions or metadata
        assert controller.agent_id == "agent1"
        assert len(controller.session_cache) == 0
        assert len(controller.meta_map) == 0


class TestSessionControllerCreate:
    @pytest.mark.asyncio
    async def test_create_new_session(self, controller, main_scope):
        # Creating a new session returns is_new=True and an active session
        is_new, session = await controller.create_if_not_exists(
            main_scope, "session-1"
        )
        assert is_new is True
        assert session.session_id == "session-1"
        assert session.is_active is True
        assert "session-1" in controller.session_cache

    @pytest.mark.asyncio
    async def test_create_returns_existing_active(
            self, controller, main_scope
    ):
        # If an active session exists, create_if_not_exists returns it with is_new=False
        is_new1, s1 = await controller.create_if_not_exists(
            main_scope, "session-1"
        )
        assert is_new1 is True
        is_new2, s2 = await controller.create_if_not_exists(
            main_scope, "session-2"
        )
        assert is_new2 is False
        assert s2.session_id == "session-1"

    @pytest.mark.asyncio
    async def test_create_duplicate_session_id_raises(
            self, controller, main_scope
    ):
        # Using the same session_id in a different scope raises ValueError
        await controller.create_if_not_exists(main_scope, "session-1")
        direct = SessionScope(scope=MainScope(), subject=DirectSubject("user1"))
        with pytest.raises(ValueError, match="already exists"):
            await controller.create_if_not_exists(direct, "session-1")

    @pytest.mark.asyncio
    async def test_create_with_custom_container_factory(
            self, controller, main_scope
    ):
        # A custom data container is used when DataContainerFactory.create is patched
        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={"custom": True})
        mock_session.update_state = MagicMock()
        mock_session.dump_state = MagicMock(return_value={"custom": True})
        custom_container = AgentSessionContainer(mock_session)
        with patch.object(DataContainerFactory, "create", return_value=custom_container):
            is_new, session = await controller.create_if_not_exists(
                main_scope,
                "session-1",
            )
        assert is_new is True
        assert session.get_data() == {"custom": True}

    @pytest.mark.asyncio
    async def test_create_persists_to_disk(
            self, controller, main_scope, base_path
    ):
        # Creating a session writes sessions.json and the session directory to disk
        await controller.create_if_not_exists(main_scope, "session-1")
        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta_file.exists()
        session_dir = base_path / "agent1" / "sessions" / "session-1"
        assert session_dir.exists()


class TestSessionControllerGet:
    @pytest.mark.asyncio
    async def test_get_scope_active_session(
            self, controller, main_scope
    ):
        # get_scope_active_session returns the active session for a scope
        await controller.create_if_not_exists(main_scope, "session-1")
        session = await controller.get_scope_active_session(main_scope)
        assert session is not None
        assert session.session_id == "session-1"

    @pytest.mark.asyncio
    async def test_get_scope_active_session_none(
            self, controller, main_scope
    ):
        # get_scope_active_session returns None when no active session exists
        session = await controller.get_scope_active_session(main_scope)
        assert session is None

    @pytest.mark.asyncio
    async def test_get_scope_sessions(self, controller, main_scope):
        # get_scope_sessions returns all cached sessions for a scope
        await controller.create_if_not_exists(main_scope, "session-1")
        sessions = await controller.get_scope_sessions(main_scope)
        assert len(sessions) == 1


class TestSessionControllerActivate:
    @pytest.mark.asyncio
    async def test_activate_session(self, controller, main_scope):
        # Activating a session makes it the active session for its scope
        await controller.create_if_not_exists(main_scope, "session-1")
        s1 = controller.session_cache["session-1"]
        s1.is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(
            main_scope, "session-2"
        )
        await controller.activate_session("session-1")
        s1_reloaded = controller.session_cache["session-1"]
        assert s1_reloaded.is_active is True

    @pytest.mark.asyncio
    async def test_activate_nonexistent_session(
            self, controller, main_scope
    ):
        # Activating a non-existent session raises KeyError
        with pytest.raises(KeyError, match="not found"):
            await controller.activate_session("nonexistent")


class TestSessionControllerFlush:
    @pytest.mark.asyncio
    async def test_flush(self, controller, main_scope, base_path):
        # flush persists all sessions and the metadata file
        await controller.create_if_not_exists(main_scope, "session-1")
        result = await controller.flush()
        assert result is True
        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta_file.exists()

    @pytest.mark.asyncio
    async def test_flush_session(self, controller, main_scope):
        # flush_session persists a specific session by ID
        await controller.create_if_not_exists(main_scope, "session-1")
        result = await controller.flush_session("session-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_flush_session_not_in_cache(self, controller):
        # flush_session returns True when the session is not in cache
        result = await controller.flush_session("nonexistent")
        assert result is True

    @pytest.mark.asyncio
    async def test_flush_scope(self, controller, main_scope):
        # flush_scope persists all sessions under a given scope
        await controller.create_if_not_exists(main_scope, "session-1")
        result = await controller.flush_scope(main_scope)
        assert result is True


class TestSessionControllerLoad:
    @pytest.mark.asyncio
    async def test_load_after_flush(
            self, controller, main_scope, base_path
    ):
        # Flushed data can be loaded by a new controller instance
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.flush()

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        result = await ctrl2.load()
        assert result is True
        assert main_scope in ctrl2.meta_map

    @pytest.mark.asyncio
    async def test_load_no_meta_file(self, base_path):
        # load returns True when no metadata file exists
        ctrl = SessionController(agent_id="agent1", base_path=base_path)
        result = await ctrl.load()
        assert result is True

    @pytest.mark.asyncio
    async def test_load_scope(self, controller, main_scope, base_path):
        # load_scope loads only sessions for the specified scope
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.flush()

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        result = await ctrl2.load_scope(main_scope)
        assert result is True
        assert main_scope in ctrl2.meta_map


class TestSessionControllerRemove:
    @pytest.mark.asyncio
    async def test_remove_session(self, controller, main_scope, base_path):
        # remove_session deletes the session from cache and metadata
        await controller.create_if_not_exists(main_scope, "session-1")
        removed = await controller.remove_session("session-1")
        assert len(removed) == 1
        assert removed[0][1].session_id == "session-1"
        assert "session-1" not in controller.session_cache

    @pytest.mark.asyncio
    async def test_remove_session_deletes_disk(
            self, controller, main_scope, base_path
    ):
        # remove_session deletes the session directory from disk
        await controller.create_if_not_exists(main_scope, "session-1")
        session_dir = base_path / "agent1" / "sessions" / "session-1"
        assert session_dir.exists()
        await controller.remove_session("session-1")
        assert not session_dir.exists()

    @pytest.mark.asyncio
    async def test_remove_scope_sessions(
            self, controller, main_scope
    ):
        # remove_scope_sessions deletes all sessions under a scope
        await controller.create_if_not_exists(main_scope, "session-1")
        removed = await controller.remove_scope_sessions(main_scope)
        assert len(removed) == 1
        assert main_scope not in controller.meta_map

    @pytest.mark.asyncio
    async def test_remove_all(self, controller, main_scope, base_path):
        # remove_all clears all sessions and deletes the base directory
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.remove_all()
        assert len(controller.session_cache) == 0
        assert len(controller.meta_map) == 0


class TestSessionControllerCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_scope_inactive_sessions(
            self, controller, main_scope
    ):
        # cleanup_scope_inactive_sessions removes inactive sessions and returns their metadata
        await controller.create_if_not_exists(main_scope, "session-1")
        s1 = controller.session_cache["session-1"]
        s1.is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(
            main_scope, "session-2"
        )
        cleaned = await controller.cleanup_scope_inactive_sessions(main_scope)
        assert len(cleaned) == 1
        assert cleaned[0][1][0].session_id == "session-1"

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_scope(self, controller, main_scope):
        # Cleaning up a scope that does not exist raises ValueError
        with pytest.raises(ValueError, match="not found"):
            await controller.cleanup_scope_inactive_sessions(main_scope)


class TestSessionControllerMeta:
    @pytest.mark.asyncio
    async def test_get_scope_meta(self, controller, main_scope):
        # get_scope_meta returns the metadata for a scope with sessions
        await controller.create_if_not_exists(main_scope, "session-1")
        meta = await controller.get_scope_meta(main_scope)
        assert meta.active_session == "session-1"

    @pytest.mark.asyncio
    async def test_get_scope_meta_empty(self, controller, main_scope):
        # get_scope_meta returns empty metadata for a scope with no sessions
        meta = await controller.get_scope_meta(main_scope)
        assert meta.active_session is None
        assert meta.sessions == []

    @pytest.mark.asyncio
    async def test_list_metas(self, controller, main_scope):
        # list_metas returns a copy of the full metadata mapping
        await controller.create_if_not_exists(main_scope, "session-1")
        metas = controller.list_metas()
        assert main_scope in metas
        del metas[main_scope]
        assert main_scope in controller.meta_map


class TestSessionControllerCreateAdvanced:
    @pytest.mark.asyncio
    async def test_create_new_scope_auto_created(self, controller, direct_scope):
        # create_if_not_exists auto-creates ScopeSessionsMeta for a new scope
        assert direct_scope not in controller.meta_map
        await controller.create_if_not_exists(direct_scope, "session-1")
        assert direct_scope in controller.meta_map
        assert controller.meta_map[direct_scope].active_session == "session-1"

    @pytest.mark.asyncio
    async def test_multi_scope_isolation(self, controller, main_scope, direct_scope):
        # Sessions in different scopes are fully isolated
        _, s1 = await controller.create_if_not_exists(main_scope, "session-1")
        _, s2 = await controller.create_if_not_exists(direct_scope, "session-2")

        assert main_scope in controller.meta_map
        assert direct_scope in controller.meta_map
        assert controller.meta_map[main_scope].active_session == "session-1"
        assert controller.meta_map[direct_scope].active_session == "session-2"

        active_main = await controller.get_scope_active_session(main_scope)
        active_direct = await controller.get_scope_active_session(direct_scope)
        assert active_main.session_id == "session-1"
        assert active_direct.session_id == "session-2"
        assert active_main is not active_direct


class TestSessionControllerFlushAdvanced:
    @pytest.mark.asyncio
    async def test_flush_empty_cache(self, controller):
        # flush returns True when session_cache is empty
        result = await controller.flush()
        assert result is True

    @pytest.mark.asyncio
    async def test_flush_session_failure(self, controller, main_scope):
        # flush_session returns False when ChainSession.flush fails
        await controller.create_if_not_exists(main_scope, "session-1")
        with patch.object(
                controller.session_cache["session-1"], "flush",
                return_value=False
        ):
            result = await controller.flush_session("session-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_flush_scope_nonexistent(self, controller, direct_scope):
        # flush_scope returns True for a scope not in meta_map
        result = await controller.flush_scope(direct_scope)
        assert result is True

    @pytest.mark.asyncio
    async def test_flush_scope_session_failure(self, controller, main_scope):
        # flush_scope returns False when a session in the scope fails to flush
        await controller.create_if_not_exists(main_scope, "session-1")
        with patch.object(
                controller.session_cache["session-1"], "flush",
                return_value=False
        ):
            result = await controller.flush_scope(main_scope)
        assert result is False

    @pytest.mark.asyncio
    async def test_flush_partial_failure(self, controller, main_scope, direct_scope):
        # flush returns False when one session flush fails
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.create_if_not_exists(direct_scope, "session-2")
        with patch.object(
                controller.session_cache["session-1"], "flush",
                return_value=False
        ):
            result = await controller.flush()
        assert result is False


class TestSessionControllerLoadAdvanced:
    @pytest.mark.asyncio
    async def test_load_active_only(self, controller, main_scope, base_path):
        # load with load_active_only=True only loads active sessions into cache
        await controller.create_if_not_exists(main_scope, "session-1")
        s1 = controller.session_cache["session-1"]
        s1.is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(main_scope, "session-2")
        await controller.flush()

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await ctrl2.load(load_active_only=True)
        assert "session-2" in ctrl2.session_cache
        assert "session-1" not in ctrl2.session_cache

    @pytest.mark.asyncio
    async def test_load_all_sessions(self, controller, main_scope, base_path):
        # load with load_active_only=False loads all sessions into cache
        await controller.create_if_not_exists(main_scope, "session-1")
        s1 = controller.session_cache["session-1"]
        s1.is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(main_scope, "session-2")
        await controller.flush()

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await ctrl2.load(load_active_only=False)
        assert "session-1" in ctrl2.session_cache
        assert "session-2" in ctrl2.session_cache

    @pytest.mark.asyncio
    async def test_load_corrupted_meta_file(self, base_path):
        # load returns False when sessions.json contains invalid JSON
        ctrl = SessionController(agent_id="agent1", base_path=base_path)
        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        meta_file.parent.mkdir(parents=True, exist_ok=True)
        meta_file.write_text("NOT VALID JSON{{{", encoding="utf-8")
        result = await ctrl.load()
        assert result is False

    @pytest.mark.asyncio
    async def test_load_partial_corrupted_scope(self, controller, main_scope, base_path):
        # load skips corrupted scope entries and loads the rest
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.flush()

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["invalid_scope_key_no_agent_prefix"] = {"sessions": []}
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        with patch.object(AgentSessionContainer, "load", _mock_load):
            result = await ctrl2.load()
        assert result is True
        assert main_scope in ctrl2.meta_map

    @pytest.mark.asyncio
    async def test_load_scope_nonexistent(self, controller, direct_scope, base_path):
        # load_scope returns True when the scope is not in sessions.json
        result = await controller.load_scope(direct_scope)
        assert result is True

    @pytest.mark.asyncio
    async def test_load_session_idempotent(self, controller, main_scope, base_path):
        # Repeated load does not duplicate sessions in cache
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.flush()

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await ctrl2.load()
            cache_size_after_first = len(ctrl2.session_cache)
            await ctrl2.load()
        assert len(ctrl2.session_cache) == cache_size_after_first


class TestSessionControllerGetAdvanced:
    @pytest.mark.asyncio
    async def test_get_scope_active_session_auto_loads(
            self, controller, main_scope, base_path
    ):
        # get_scope_active_session auto-loads session not in cache
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.flush()

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await ctrl2.load()
        ctrl2.session_cache.clear()

        with patch.object(AgentSessionContainer, "load", _mock_load):
            session = await ctrl2.get_scope_active_session(main_scope)
        assert session is not None
        assert session.session_id == "session-1"

    @pytest.mark.asyncio
    async def test_get_scope_sessions_unknown_scope(self, controller, direct_scope):
        # get_scope_sessions returns empty list for unknown scope
        sessions = await controller.get_scope_sessions(direct_scope)
        assert sessions == []

    @pytest.mark.asyncio
    async def test_get_scope_sessions_unloaded_not_in_result(
            self, controller, main_scope, base_path
    ):
        # get_scope_sessions only returns sessions already in cache
        await controller.create_if_not_exists(main_scope, "session-1")
        s1 = controller.session_cache["session-1"]
        s1.is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(main_scope, "session-2")
        await controller.flush()

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await ctrl2.load(load_active_only=True)
        sessions = await ctrl2.get_scope_sessions(main_scope)
        session_ids = [s.session_id for s in sessions]
        assert "session-2" in session_ids
        assert "session-1" not in session_ids


class TestSessionControllerActivateAdvanced:
    @pytest.mark.asyncio
    async def test_activate_from_meta_map(self, controller, main_scope, base_path):
        # activate_session finds and loads session from meta_map when not in cache
        await controller.create_if_not_exists(main_scope, "session-1")
        s1 = controller.session_cache["session-1"]
        s1.is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(main_scope, "session-2")
        await controller.flush()

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        ctrl2 = SessionController(agent_id="agent1", base_path=base_path)
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await ctrl2.load(load_active_only=True)
        assert "session-1" not in ctrl2.session_cache

        with patch.object(AgentSessionContainer, "load", _mock_load):
            await ctrl2.activate_session("session-1")
        assert ctrl2.session_cache["session-1"].is_active is True

    @pytest.mark.asyncio
    async def test_activate_persists(self, controller, main_scope, base_path):
        # activate_session flushes the session and writes meta file
        await controller.create_if_not_exists(main_scope, "session-1")
        s1 = controller.session_cache["session-1"]
        s1.is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(main_scope, "session-2")
        await controller.activate_session("session-1")

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta_file.exists()


class TestSessionControllerRemoveAdvanced:
    @pytest.mark.asyncio
    async def test_remove_session_without_scope(
            self, controller, main_scope, direct_scope
    ):
        # remove_session without scope searches all scopes
        await controller.create_if_not_exists(main_scope, "session-1")
        removed = await controller.remove_session("session-1")
        assert len(removed) == 1
        assert "session-1" not in controller.session_cache

    @pytest.mark.asyncio
    async def test_remove_nonexistent_session(self, controller):
        # remove_session returns empty list for nonexistent ID
        removed = await controller.remove_session("nonexistent")
        assert removed == []

    @pytest.mark.asyncio
    async def test_remove_active_session_clears_active(
            self, controller, main_scope
    ):
        # Removing the active session clears active_session in scope meta
        await controller.create_if_not_exists(main_scope, "session-1")
        assert controller.meta_map[main_scope].active_session == "session-1"
        await controller.remove_session("session-1")
        assert controller.meta_map[main_scope].active_session is None

    @pytest.mark.asyncio
    async def test_remove_scope_sessions_nonexistent(
            self, controller, direct_scope
    ):
        # remove_scope_sessions returns empty list for unknown scope
        removed = await controller.remove_scope_sessions(direct_scope)
        assert removed == []

    @pytest.mark.asyncio
    async def test_remove_session_updates_meta_file(
            self, controller, main_scope, base_path
    ):
        # remove_session updates sessions.json on disk
        await controller.create_if_not_exists(main_scope, "session-1")
        await controller.remove_session("session-1")

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta_file.exists()
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for scope_data in data.values():
            session_ids = [s["session_id"] for s in scope_data.get("sessions", [])]
            assert "session-1" not in session_ids


class TestSessionControllerCleanupAdvanced:
    @pytest.mark.asyncio
    async def test_cleanup_all_active(self, controller, main_scope):
        # cleanup with all sessions active removes nothing
        await controller.create_if_not_exists(main_scope, "session-1")
        cleaned = await controller.cleanup_scope_inactive_sessions(main_scope)
        assert len(cleaned) == 1
        assert len(cleaned[0][1]) == 0

    @pytest.mark.asyncio
    async def test_cleanup_all_inactive(self, controller, main_scope):
        # cleanup with all sessions inactive removes all
        await controller.create_if_not_exists(main_scope, "session-1")
        controller.session_cache["session-1"].is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        cleaned = await controller.cleanup_scope_inactive_sessions(main_scope)
        assert len(cleaned[0][1]) == 1
        assert len(controller.session_cache) == 0


class TestSessionControllerConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_create_same_scope(self, controller, main_scope):
        # Concurrent create_if_not_exists on same scope is serialized by lock
        results = await asyncio.gather(
            controller.create_if_not_exists(main_scope, "session-1"),
            controller.create_if_not_exists(main_scope, "session-2"),
        )
        new_counts = [r[0] for r in results]
        assert new_counts.count(True) == 1
        assert new_counts.count(False) == 1

    @pytest.mark.asyncio
    async def test_concurrent_flush_and_load(
            self, controller, main_scope, base_path
    ):
        # Concurrent flush and load do not corrupt data
        await controller.create_if_not_exists(main_scope, "session-1")
        await asyncio.gather(
            controller.flush(),
            controller.load(),
        )
        assert "session-1" in controller.session_cache

    @pytest.mark.asyncio
    async def test_concurrent_remove_and_get_sessions(
            self, controller, main_scope
    ):
        # Concurrent remove and query are serialized by lock
        await controller.create_if_not_exists(main_scope, "session-1")
        controller.session_cache["session-1"].is_active = False
        controller.meta_map[main_scope].deactivate_all_sessions()
        await controller.create_if_not_exists(main_scope, "session-2")

        results = await asyncio.gather(
            controller.cleanup_scope_inactive_sessions(main_scope),
            controller.get_scope_sessions(main_scope),
        )
        cleaned = results[0]
        sessions = results[1]
        assert len(cleaned) > 0
        assert all(s.is_active for s in sessions)


class TestSessionControllerSecurity:
    @pytest.mark.asyncio
    async def test_sessions_json_no_sensitive_data(
            self, controller, main_scope, base_path
    ):
        # sessions.json does not contain business data; data is in state.data
        await controller.create_if_not_exists(main_scope, "session-1")
        session = controller.session_cache["session-1"]
        await session.update_data({"secret": "sensitive_value"})
        await controller.flush()

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        with open(meta_file, "r", encoding="utf-8") as f:
            meta_content = f.read()
        assert "sensitive_value" not in meta_content

    @pytest.mark.asyncio
    async def test_path_traversal_session_id(
            self, controller, main_scope, base_path
    ):
        # pathlib.Path normalizes ../ so the session directory stays
        # within the agent base directory, but escapes the sessions/ subdir
        _, session = await controller.create_if_not_exists(
            main_scope, "../escape_session"
        )
        resolved_base = (base_path / "agent1").resolve()
        resolved_session = (
            base_path / "agent1" / "sessions" / session.session_id
        ).resolve()
        assert str(resolved_session).startswith(str(resolved_base))


class TestSessionControllerResilience:
    @pytest.mark.asyncio
    async def test_session_dir_deleted_externally(
            self, controller, main_scope, base_path
    ):
        # flush auto-creates directory when deleted externally
        await controller.create_if_not_exists(main_scope, "session-1")
        session = controller.session_cache["session-1"]
        import shutil
        session_dir = base_path / "agent1" / "sessions" / "session-1"
        shutil.rmtree(session_dir, ignore_errors=True)
        result = await session.flush()
        assert result is True
        assert session_dir.exists()
