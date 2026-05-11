# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

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
