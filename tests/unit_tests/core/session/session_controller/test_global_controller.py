# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.session.session_controller.data_container import (
    AgentSessionContainer,
    DataContainerFactory,
    Permission,
    SharingPolicy,
)
from openjiuwen.core.session.session_controller.global_controller import (
    GlobalSessionConfig,
    GlobalSessionController,
)
from openjiuwen.core.session.session_controller.scope import (
    MainScope,
    DirectSubject,
    SessionScope,
)
from openjiuwen.core.session.session_controller.scope_factory import SessionScopeFactory
from openjiuwen.core.session.session_controller.session_controller import SessionController


@pytest.fixture(autouse=True)
def reset_singleton():
    GlobalSessionController._instances = {}
    yield
    GlobalSessionController._instances = {}


@pytest.fixture
def base_path(tmp_path):
    return tmp_path / "agents"


@pytest.fixture
def global_controller(base_path):
    ctrl = GlobalSessionController()
    ctrl.base_path = base_path
    ctrl._data_container_type = "agent"
    return ctrl


@pytest.fixture
def main_scope():
    return SessionScope(scope=MainScope())


@pytest.fixture
def direct_scope():
    return SessionScope(scope=MainScope(), subject=DirectSubject("user1"))


class TestGlobalSessionControllerSingleton:
    def test_singleton(self, base_path):
        # Two instantiations return the same object
        ctrl1 = GlobalSessionController()
        ctrl1.base_path = base_path
        ctrl2 = GlobalSessionController()
        assert ctrl1 is ctrl2


class TestGlobalSessionConfig:
    def test_defaults(self):
        config = GlobalSessionConfig()
        assert config.base_path == "./agents"

    def test_custom(self):
        # Custom config values override defaults
        config = GlobalSessionConfig(
            base_path="/custom/path"
        )
        assert config.base_path == "/custom/path"


class TestGlobalSessionControllerConfig:
    def test_set_config_dict(self, global_controller, base_path):
        # Config can be set via a plain dict
        global_controller.set_config({"base_path": str(base_path), "data_container_type": "agent"})
        assert global_controller.base_path == base_path
        assert global_controller._data_container_type == "agent"

    def test_set_config_object(self, global_controller, base_path):
        # Config can be set via a GlobalSessionConfig instance
        config = GlobalSessionConfig(base_path=str(base_path))
        global_controller.set_config(config)
        assert global_controller.base_path == base_path
        assert global_controller._data_container_type == "agent"


class TestGlobalSessionControllerAgentManagement:
    @pytest.mark.asyncio
    async def test_create_if_not_exist_agent_new(
            self, global_controller, base_path
    ):
        # Creating a new agent returns is_new=True and a SessionController
        is_new, ctrl = await global_controller.create_if_not_exist_agent(
            "agent1"
        )
        assert is_new is True
        assert isinstance(ctrl, SessionController)
        assert "agent1" in global_controller.controllers

    @pytest.mark.asyncio
    async def test_create_if_not_exist_agent_existing(
            self, global_controller, base_path
    ):
        # Creating the same agent again returns is_new=False
        await global_controller.create_if_not_exist_agent("agent1")
        is_new, ctrl = await global_controller.create_if_not_exist_agent(
            "agent1"
        )
        assert is_new is False

    @pytest.mark.asyncio
    async def test_get_agent(self, global_controller, base_path):
        # get_agent returns the controller if it exists, None otherwise
        await global_controller.create_if_not_exist_agent("agent1")
        ctrl = global_controller.get_agent("agent1")
        assert ctrl is not None
        assert global_controller.get_agent("nonexistent") is None

    @pytest.mark.asyncio
    async def test_remove_agent(self, global_controller, base_path):
        # Removing an existing agent returns True and clears it from controllers
        await global_controller.create_if_not_exist_agent("agent1")
        result = await global_controller.remove_agent("agent1")
        assert result is True
        assert "agent1" not in global_controller.controllers

    @pytest.mark.asyncio
    async def test_remove_nonexistent_agent(self, global_controller):
        # Removing a non-existent agent returns False
        result = await global_controller.remove_agent("nonexistent")
        assert result is False


class TestGlobalSessionControllerPersistence:
    @pytest.mark.asyncio
    async def test_load_agent(self, global_controller, base_path, main_scope):
        # Flushed data can be reloaded after clearing the in-memory cache
        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        is_new, ctrl = await global_controller.create_if_not_exist_agent(
            "agent1"
        )
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await ctrl.flush()

        global_controller.controllers.clear()
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await global_controller.load_agent("agent1")
        assert "agent1" in global_controller.controllers

    @pytest.mark.asyncio
    async def test_flush_agent(self, global_controller, base_path, main_scope):
        # flush_agent writes sessions.json to disk
        is_new, ctrl = await global_controller.create_if_not_exist_agent(
            "agent1"
        )
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_agent("agent1")
        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta_file.exists()

    @pytest.mark.asyncio
    async def test_flush_all(self, global_controller, base_path, main_scope):
        # flush_all persists every registered agent's data
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        await ctrl1.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_all()
        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta_file.exists()

    @pytest.mark.asyncio
    async def test_flush_session(self, global_controller, base_path, main_scope):
        # flush_session flushes a specific session by ID
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_session("session-1")

    @pytest.mark.asyncio
    async def test_flush_scope(self, global_controller, base_path, main_scope):
        # flush_scope flushes all sessions under a given scope
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_scope(main_scope)


class TestGlobalSessionControllerCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_agent_inactive_sessions(
            self, global_controller, base_path, main_scope
    ):
        # Inactive sessions are cleaned up and reported per agent
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        await ctrl.create_if_not_exists(main_scope, "session-1")
        s1 = ctrl.session_cache["session-1"]
        s1.is_active = False
        ctrl.meta_map[main_scope].deactivate_all_sessions()
        await ctrl.create_if_not_exists(main_scope, "session-2")
        result = await global_controller.cleanup_agent_inactive_sessions(
            "agent1"
        )
        assert "agent1" in result

    @pytest.mark.asyncio
    async def test_cleanup_agent_inactive_sessions_not_found(
            self, global_controller
    ):
        # Cleaning up a non-existent agent raises ValueError
        with pytest.raises(ValueError, match="not found"):
            await global_controller.cleanup_agent_inactive_sessions(
                "nonexistent"
            )

    @pytest.mark.asyncio
    async def test_remove_all(self, global_controller, base_path, main_scope):
        # remove_all clears all controllers and deletes the base directory
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.remove_all()
        assert len(global_controller.controllers) == 0


class TestGlobalSessionControllerOrphanCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_orphan_dirs_dry_run(
            self, global_controller, base_path, main_scope
    ):
        # dry_run=True detects orphans without deleting them
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        await ctrl.create_if_not_exists(main_scope, "session-1")
        orphan_dir = base_path / "agent1" / "sessions" / "orphan-session"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "state.data").write_text("{}", encoding="utf-8")

        result = await global_controller.cleanup_orphan_files(
            agent_id="agent1", dry_run=True
        )
        assert "agent1" in result
        assert "orphan-session" in result["agent1"]
        assert orphan_dir.exists()

    @pytest.mark.asyncio
    async def test_cleanup_orphan_dirs_delete(
            self, global_controller, base_path, main_scope
    ):
        # dry_run=False actually deletes orphan directories
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        await ctrl.create_if_not_exists(main_scope, "session-1")
        orphan_dir = base_path / "agent1" / "sessions" / "orphan-session"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "state.data").write_text("{}", encoding="utf-8")

        result = await global_controller.cleanup_orphan_files(
            agent_id="agent1", dry_run=False
        )
        assert "agent1" in result
        assert not orphan_dir.exists()


class TestGlobalSessionControllerMultiAgentIntegration:
    @pytest.mark.asyncio
    async def test_cross_agent_downstream_visibility(
            self, global_controller, base_path
    ):
        # Two agents with a downstream relationship: agent1 -> agent2
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        main_scope = SessionScope(scope=MainScope())

        _, s1 = await ctrl1.create_if_not_exists(main_scope, "session-1")
        _, s2 = await ctrl2.create_if_not_exists(main_scope, "session-2")

        s1.add_downstream("agent2", "session-2", SharingPolicy(permission=Permission.READ))

        assert s1.can_see("agent2", "session-2") is True
        assert s2.can_see("agent1", "session-1") is False

    @pytest.mark.asyncio
    async def test_load_all_multiple_agents(
            self, global_controller, base_path
    ):
        # load_all discovers and loads all agent directories from disk
        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        main_scope = SessionScope(scope=MainScope())

        await ctrl1.create_if_not_exists(main_scope, "session-1")
        await ctrl2.create_if_not_exists(main_scope, "session-2")
        await global_controller.flush_all()

        global_controller.controllers.clear()
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await global_controller.load_all()

        assert "agent1" in global_controller.controllers
        assert "agent2" in global_controller.controllers

    @pytest.mark.asyncio
    async def test_load_scope_across_agents(
            self, global_controller, base_path
    ):
        # load_scope loads the specified scope for all registered agents
        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        direct_scope = SessionScopeFactory.create_direct("user1")
        main_scope = SessionScope(scope=MainScope())

        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        await ctrl1.create_if_not_exists(direct_scope, "session-d1")
        await ctrl1.create_if_not_exists(main_scope, "session-m1")
        await ctrl2.create_if_not_exists(direct_scope, "session-d2")
        await global_controller.flush_all()

        global_controller.controllers.clear()
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await global_controller.load_all()
            await global_controller.load_scope(direct_scope)

        ctrl1 = global_controller.get_agent("agent1")
        ctrl2 = global_controller.get_agent("agent2")
        assert ctrl1 is not None
        assert ctrl2 is not None

    @pytest.mark.asyncio
    async def test_flush_session_cross_agent(
            self, global_controller, base_path
    ):
        # flush_session finds the session across all agent caches
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl1.create_if_not_exists(main_scope, "shared-session-id")

        await global_controller.flush_session("shared-session-id")

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta_file.exists()

    @pytest.mark.asyncio
    async def test_flush_session_not_found(
            self, global_controller, base_path
    ):
        # flush_session gracefully handles session_id not in any cache
        await global_controller.create_if_not_exist_agent("agent1")
        await global_controller.flush_session("nonexistent-session")

    @pytest.mark.asyncio
    async def test_flush_scope_cross_agent(
            self, global_controller, base_path
    ):
        # flush_scope flushes the given scope across all agents
        direct_scope = SessionScopeFactory.create_direct("user1")
        main_scope = SessionScope(scope=MainScope())

        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        await ctrl1.create_if_not_exists(direct_scope, "session-d1")
        await ctrl2.create_if_not_exists(direct_scope, "session-d2")

        await global_controller.flush_scope(direct_scope)

        for agent_id in ["agent1", "agent2"]:
            meta_file = base_path / agent_id / "sessions" / "sessions.json"
            assert meta_file.exists()

    @pytest.mark.asyncio
    async def test_remove_agent_deletes_disk_directory(
            self, global_controller, base_path
    ):
        # remove_agent deletes the agent directory on disk
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_agent("agent1")

        agent_dir = base_path / "agent1"
        assert agent_dir.exists()

        await global_controller.remove_agent("agent1")
        assert not agent_dir.exists()
        assert global_controller.get_agent("agent1") is None

    @pytest.mark.asyncio
    async def test_cleanup_scope_inactive_sessions_cross_agent(
            self, global_controller, base_path
    ):
        # cleanup_scope_inactive_sessions cleans the scope across all agents
        direct_scope = SessionScopeFactory.create_direct("user1")

        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        await ctrl1.create_if_not_exists(direct_scope, "session-d1")
        await ctrl2.create_if_not_exists(direct_scope, "session-d2")

        s1 = ctrl1.session_cache["session-d1"]
        s1.is_active = False
        ctrl1.meta_map[direct_scope].deactivate_all_sessions()
        await ctrl1.create_if_not_exists(direct_scope, "session-d1-new")

        s2 = ctrl2.session_cache["session-d2"]
        s2.is_active = False
        ctrl2.meta_map[direct_scope].deactivate_all_sessions()
        await ctrl2.create_if_not_exists(direct_scope, "session-d2-new")

        result = await global_controller.cleanup_scope_inactive_sessions(direct_scope)
        assert "agent1" in result
        assert "agent2" in result

    @pytest.mark.asyncio
    async def test_cleanup_orphan_files_all_agents(
            self, global_controller, base_path
    ):
        # cleanup_orphan_files without agent_id scans all agents
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        main_scope = SessionScope(scope=MainScope())
        await ctrl1.create_if_not_exists(main_scope, "session-1")
        await ctrl2.create_if_not_exists(main_scope, "session-2")
        await global_controller.flush_all()

        for agent_id in ["agent1", "agent2"]:
            orphan_dir = base_path / agent_id / "sessions" / f"orphan-{agent_id}"
            orphan_dir.mkdir(parents=True, exist_ok=True)
            (orphan_dir / "state.data").write_text("{}", encoding="utf-8")

        result = await global_controller.cleanup_orphan_files(dry_run=True)
        assert "agent1" in result
        assert "agent2" in result
        assert f"orphan-agent1" in result["agent1"]
        assert f"orphan-agent2" in result["agent2"]

    @pytest.mark.asyncio
    async def test_cleanup_orphan_files_no_orphans(
            self, global_controller, base_path
    ):
        # cleanup_orphan_files returns empty when there are no orphans
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_all()

        result = await global_controller.cleanup_orphan_files(dry_run=True)
        assert result == {}


class TestGlobalSessionControllerConvenienceMethods:
    @pytest.fixture
    def mock_container(self):
        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={"key": "value"})
        mock_session.update_state = MagicMock()
        return AgentSessionContainer(mock_session)

    @pytest.mark.asyncio
    async def test_create_direct_session(
            self, global_controller, base_path, mock_container
    ):
        # create_direct_session creates a session with DirectSubject scope
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            is_new, session = await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        assert is_new is True
        assert session.session_id == "session-1"
        assert "direct:user1" in str(session.session_scope)

    @pytest.mark.asyncio
    async def test_create_direct_session_returns_existing(
            self, global_controller, base_path, mock_container
    ):
        # create_direct_session returns existing active session if one exists
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            is_new1, s1 = await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
            is_new2, s2 = await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-2"
            )
        assert is_new1 is True
        assert is_new2 is False
        assert s2.session_id == "session-1"

    @pytest.mark.asyncio
    async def test_create_group_session(
            self, global_controller, base_path, mock_container
    ):
        # create_group_session creates a session with GroupSubject scope
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            is_new, session = await GlobalSessionController.create_group_session(
                agent_id="agent1", group_id="group1", session_id="session-1"
            )
        assert is_new is True
        assert "group:group1" in str(session.session_scope)

    @pytest.mark.asyncio
    async def test_get_direct_session_data(
            self, global_controller, base_path, mock_container
    ):
        # get_direct_session_data returns data for the active direct session
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        data = await GlobalSessionController.get_direct_session_data(
            agent_id="agent1", user_id="user1"
        )
        assert data is not None

    @pytest.mark.asyncio
    async def test_get_direct_session_data_no_agent(
            self, global_controller, base_path
    ):
        # get_direct_session_data returns None when agent does not exist
        data = await GlobalSessionController.get_direct_session_data(
            agent_id="nonexistent", user_id="user1"
        )
        assert data is None

    @pytest.mark.asyncio
    async def test_get_direct_session_data_no_session(
            self, global_controller, base_path
    ):
        # get_direct_session_data returns None when no active session exists
        await global_controller.create_if_not_exist_agent("agent1")
        data = await GlobalSessionController.get_direct_session_data(
            agent_id="agent1", user_id="user1"
        )
        assert data is None

    @pytest.mark.asyncio
    async def test_update_direct_session_data(
            self, global_controller, base_path, mock_container
    ):
        # update_direct_session_data updates data in the active direct session
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        result = await GlobalSessionController.update_direct_session_data(
            agent_id="agent1", user_id="user1", data={"new_key": "new_value"}
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_update_direct_session_data_no_agent(
            self, global_controller, base_path
    ):
        # update_direct_session_data returns False when agent does not exist
        result = await GlobalSessionController.update_direct_session_data(
            agent_id="nonexistent", user_id="user1", data={}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_add_direct_session_downstream(
            self, global_controller, base_path, mock_container
    ):
        # add_direct_session_downstream links caller to target session
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
            await GlobalSessionController.create_direct_session(
                agent_id="agent2", user_id="user2", session_id="session-2"
            )
        result = await GlobalSessionController.add_direct_session_downstream(
            caller_agent_id="agent1",
            caller_user_id="user1",
            target_agent_id="agent2",
            target_user_id="user2",
        )
        assert result is True

        ctrl1 = global_controller.get_agent("agent1")
        s1 = ctrl1.session_cache["session-1"]
        assert s1.has_downstream("agent2", "session-2")

    @pytest.mark.asyncio
    async def test_add_direct_session_downstream_missing_target(
            self, global_controller, base_path, mock_container
    ):
        # add_direct_session_downstream returns False when target agent has no active session
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        result = await GlobalSessionController.add_direct_session_downstream(
            caller_agent_id="agent1",
            caller_user_id="user1",
            target_agent_id="agent2",
            target_user_id="user2",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_create_group_session(
            self, global_controller, base_path, mock_container
    ):
        # create_group_session creates a session with GroupSubject scope
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            is_new, session = await GlobalSessionController.create_group_session(
                agent_id="agent1", group_id="grp1", session_id="session-1"
            )
        assert is_new is True
        assert session.session_id == "session-1"
        assert "group:grp1" in str(session.session_scope)

    @pytest.mark.asyncio
    async def test_add_direct_session_downstream_caller_not_exist(
            self, global_controller, base_path, mock_container
    ):
        # add_direct_session_downstream returns False when caller agent does not exist
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent2", user_id="user2", session_id="session-2"
            )
        result = await GlobalSessionController.add_direct_session_downstream(
            caller_agent_id="nonexistent",
            caller_user_id="user1",
            target_agent_id="agent2",
            target_user_id="user2",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_add_direct_session_downstream_caller_no_active_session(
            self, global_controller, base_path, mock_container
    ):
        # add_direct_session_downstream returns False when caller has no active session
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent2", user_id="user2", session_id="session-2"
            )
        await global_controller.create_if_not_exist_agent("agent1")
        result = await GlobalSessionController.add_direct_session_downstream(
            caller_agent_id="agent1",
            caller_user_id="user1",
            target_agent_id="agent2",
            target_user_id="user2",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_add_direct_session_downstream_target_no_active_session(
            self, global_controller, base_path, mock_container
    ):
        # add_direct_session_downstream returns False when target has no active session
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        await global_controller.create_if_not_exist_agent("agent2")
        result = await GlobalSessionController.add_direct_session_downstream(
            caller_agent_id="agent1",
            caller_user_id="user1",
            target_agent_id="agent2",
            target_user_id="user2",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_user_sessions(
            self, global_controller, base_path, mock_container
    ):
        # cleanup_user_sessions cleans inactive sessions for a user
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        ctrl = global_controller.get_agent("agent1")
        s1 = ctrl.session_cache["session-1"]
        s1.is_active = False
        direct_scope = SessionScopeFactory.create_direct("user1")
        ctrl.meta_map[direct_scope].deactivate_all_sessions()
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await ctrl.create_if_not_exists(direct_scope, "session-2")

        result = await GlobalSessionController.cleanup_user_sessions(
            agent_id="agent1", user_id="user1"
        )
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_cleanup_user_sessions_no_agent(
            self, global_controller, base_path
    ):
        # cleanup_user_sessions returns empty list when agent does not exist
        result = await GlobalSessionController.cleanup_user_sessions(
            agent_id="nonexistent", user_id="user1"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_user_session_history(
            self, global_controller, base_path, mock_container
    ):
        # get_user_session_history returns cached sessions for a user
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        result = await GlobalSessionController.get_user_session_history(
            agent_id="agent1", user_id="user1"
        )
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_get_user_session_history_no_agent(
            self, global_controller, base_path
    ):
        # get_user_session_history returns empty list when agent does not exist
        result = await GlobalSessionController.get_user_session_history(
            agent_id="nonexistent", user_id="user1"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_flush_user_session(
            self, global_controller, base_path, mock_container
    ):
        # flush_user_session flushes the scope for a user
        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-1"
            )
        result = await GlobalSessionController.flush_user_session(
            agent_id="agent1", user_id="user1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_flush_user_session_no_agent(
            self, global_controller, base_path
    ):
        # flush_user_session returns False when agent does not exist
        result = await GlobalSessionController.flush_user_session(
            agent_id="nonexistent", user_id="user1"
        )
        assert result is False


class TestGlobalSessionControllerVisualizeCallChain:
    @pytest.mark.asyncio
    async def test_visualize_call_chain_basic(
            self, global_controller, base_path
    ):
        # visualize_call_chain produces a tree showing downstream relationships
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        _, s1 = await ctrl1.create_if_not_exists(main_scope, "session-1")

        s1.add_downstream("agent2", "session-2", SharingPolicy(permission=Permission.READ))

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "ChainSession Call Chain Visualization" in result
        assert "agent2" in result

    @pytest.mark.asyncio
    async def test_visualize_call_chain_agent_not_found(
            self, global_controller, base_path
    ):
        # visualize_call_chain returns error message for unknown agent
        result = await GlobalSessionController.visualize_call_chain(
            agent_id="nonexistent", session_id="session-1"
        )
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_visualize_call_chain_session_not_found(
            self, global_controller, base_path
    ):
        # visualize_call_chain returns error message for unknown session
        await global_controller.create_if_not_exist_agent("agent1")
        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="nonexistent"
        )
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_visualize_call_chain_with_field_scopes(
            self, global_controller, base_path
    ):
        # visualize_call_chain shows field scopes when present
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        _, s1 = await ctrl1.create_if_not_exists(main_scope, "session-1")

        s1.add_downstream(
            "agent2", "session-2",
            SharingPolicy(permission=Permission.READ, field_scopes={"field1", "field2"})
        )

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "field1" in result or "field2" in result

    @pytest.mark.asyncio
    async def test_visualize_full_header_info(
            self, global_controller, base_path
    ):
        # Full header includes title, separator, session key, status, depth line
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        _, session = await ctrl.create_if_not_exists(main_scope, "session-1")

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        lines = result.split("\n")

        assert lines[0] == "ChainSession Call Chain Visualization"
        assert lines[1] == "=" * 50
        assert "agent:agent1:main" in lines[2]
        assert "session-" in lines[2]
        assert lines[3] == "Status: Active"
        assert lines[4] == ""
        assert "Call chain relationships (depth: 3)" in lines[5]
        assert lines[6] == "-" * 50

    @pytest.mark.asyncio
    async def test_visualize_inactive_session_status(
            self, global_controller, base_path
    ):
        # Inactive sessions show "Status: Inactive"
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        _, session = await ctrl.create_if_not_exists(main_scope, "session-1")
        session.is_active = False

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "Status: Inactive" in result

    @pytest.mark.asyncio
    async def test_visualize_no_downstreams(
            self, global_controller, base_path
    ):
        # A session with no downstreams produces header only, no tree lines
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        lines = result.split("\n")
        header_end_idx = lines.index("-" * 50)
        tree_lines = lines[header_end_idx + 1:]
        tree_lines = [l for l in tree_lines if l.strip()]
        assert len(tree_lines) == 0

    @pytest.mark.asyncio
    async def test_visualize_single_downstream_all_fields(
            self, global_controller, base_path
    ):
        # Single downstream with no field_scopes shows "All fields"
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        _, s1 = await ctrl.create_if_not_exists(main_scope, "session-1")

        s1.add_downstream(
            "agent2", "session-2",
            SharingPolicy(permission=Permission.READ)
        )

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "├─► agent2" in result
        assert "Permissions: READ" in result
        assert "Field scope: All fields" in result
        assert "(not loaded)" in result

    @pytest.mark.asyncio
    async def test_visualize_single_downstream_with_field_scopes(
            self, global_controller, base_path
    ):
        # Single downstream with field_scopes shows the field set
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        _, s1 = await ctrl.create_if_not_exists(main_scope, "session-1")

        s1.add_downstream(
            "agent2", "session-2",
            SharingPolicy(permission=Permission.READ, field_scopes={"name", "age"})
        )

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "Permissions: READ" in result
        assert "name" in result
        assert "age" in result
        assert "All fields" not in result

    @pytest.mark.asyncio
    async def test_visualize_multiple_downstreams(
            self, global_controller, base_path
    ):
        # Multiple downstreams each appear as separate tree branches
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        _, s1 = await ctrl.create_if_not_exists(main_scope, "session-1")

        s1.add_downstream("agent2", "session-2", SharingPolicy(permission=Permission.READ))
        s1.add_downstream("agent3", "session-3", SharingPolicy(permission=Permission.READ))

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "agent2" in result
        assert "agent3" in result
        assert result.count("├─►") >= 2

    @pytest.mark.asyncio
    async def test_visualize_recursive_downstream_loaded(
            self, global_controller, base_path
    ):
        # Recursive downstream: agent1/s1 -> agent2/s2 -> agent3/s3 (all loaded)
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        _, ctrl3 = await global_controller.create_if_not_exist_agent("agent3")
        main_scope = SessionScope(scope=MainScope())

        _, s1 = await ctrl1.create_if_not_exists(main_scope, "session-1")
        _, s2 = await ctrl2.create_if_not_exists(main_scope, "session-2")
        _, s3 = await ctrl3.create_if_not_exists(main_scope, "session-3")

        s1.add_downstream("agent2", "session-2", SharingPolicy(permission=Permission.READ))
        s2.add_downstream("agent3", "session-3", SharingPolicy(permission=Permission.READ))

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1", depth=3
        )
        assert "agent2" in result
        assert "agent3" in result
        assert "(not loaded)" not in result

    @pytest.mark.asyncio
    async def test_visualize_recursive_downstream_partial_loaded(
            self, global_controller, base_path
    ):
        # Recursive downstream: agent1/s1 -> agent2/s2 (loaded), agent2/s2 -> agent3/s3 (not loaded)
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        main_scope = SessionScope(scope=MainScope())

        _, s1 = await ctrl1.create_if_not_exists(main_scope, "session-1")
        _, s2 = await ctrl2.create_if_not_exists(main_scope, "session-2")

        s1.add_downstream("agent2", "session-2", SharingPolicy(permission=Permission.READ))
        s2.add_downstream("agent3", "session-3", SharingPolicy(permission=Permission.READ))

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1", depth=3
        )
        assert "agent2" in result
        assert "agent3" in result
        assert "(not loaded)" in result

    @pytest.mark.asyncio
    async def test_visualize_custom_depth(
            self, global_controller, base_path
    ):
        # Custom depth=1 stops recursion at the first level
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        _, ctrl3 = await global_controller.create_if_not_exist_agent("agent3")
        main_scope = SessionScope(scope=MainScope())

        _, s1 = await ctrl1.create_if_not_exists(main_scope, "session-1")
        _, s2 = await ctrl2.create_if_not_exists(main_scope, "session-2")
        _, s3 = await ctrl3.create_if_not_exists(main_scope, "session-3")

        s1.add_downstream("agent2", "session-2", SharingPolicy(permission=Permission.READ))
        s2.add_downstream("agent3", "session-3", SharingPolicy(permission=Permission.READ))

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1", depth=1
        )
        assert "agent2" in result
        assert "Call chain relationships (depth: 1)" in result

    @pytest.mark.asyncio
    async def test_visualize_session_id_truncation(
            self, global_controller, base_path
    ):
        # Long session IDs are truncated to first 8 chars with "..."
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        long_id = "abcdef1234567890"
        _, session = await ctrl.create_if_not_exists(main_scope, long_id)

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id=long_id
        )
        assert "abcdef12..." in result

    @pytest.mark.asyncio
    async def test_visualize_direct_scope_in_header(
            self, global_controller, base_path
    ):
        # Session with DirectSubject scope shows full scope key in header
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        direct_scope = SessionScopeFactory.create_direct("user1")
        _, session = await ctrl.create_if_not_exists(direct_scope, "session-1")

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "agent:agent1:main:direct:user1" in result

    @pytest.mark.asyncio
    async def test_visualize_group_scope_in_header(
            self, global_controller, base_path
    ):
        # Session with GroupSubject scope shows full scope key in header
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        group_scope = SessionScopeFactory.create_group("group1")
        _, session = await ctrl.create_if_not_exists(group_scope, "session-1")

        result = await GlobalSessionController.visualize_call_chain(
            agent_id="agent1", session_id="session-1"
        )
        assert "agent:agent1:main:group:group1" in result


class TestGlobalSessionControllerAdvanced:
    @pytest.mark.asyncio
    async def test_flush_agent_not_found(self, global_controller):
        # flush_agent gracefully handles nonexistent agent
        await global_controller.flush_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_load_all_no_directory(self, global_controller, tmp_path):
        # load_all with nonexistent base_path does not crash
        global_controller.base_path = tmp_path / "nonexistent"
        await global_controller.load_all()
        assert len(global_controller.controllers) == 0

    @pytest.mark.asyncio
    async def test_cleanup_orphan_files_agent_dir_exists_no_controller(
            self, global_controller, base_path
    ):
        # cleanup_orphan_files scans agent directory even without a controller
        agent_sessions_dir = base_path / "orphan_agent" / "sessions"
        orphan_dir = agent_sessions_dir / "orphan-s1"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "state.data").write_text("{}", encoding="utf-8")

        meta_file = base_path / "orphan_agent" / "sessions" / "sessions.json"
        meta_file.parent.mkdir(parents=True, exist_ok=True)
        meta_file.write_text("{}", encoding="utf-8")

        result = await global_controller.cleanup_orphan_files(
            agent_id="orphan_agent", dry_run=True
        )
        assert "orphan_agent" in result

    @pytest.mark.asyncio
    async def test_cleanup_orphan_files_corrupted_meta(
            self, global_controller, base_path
    ):
        # cleanup_orphan_files handles corrupted sessions.json
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_all()

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        meta_file.write_text("NOT VALID JSON{{{{", encoding="utf-8")

        orphan_dir = base_path / "agent1" / "sessions" / "orphan-s1"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "state.data").write_text("{}", encoding="utf-8")

        result = await global_controller.cleanup_orphan_files(dry_run=True)
        assert "agent1" in result
        assert "orphan-s1" in result["agent1"]

    @pytest.mark.asyncio
    async def test_remove_all_clears_base_directory(
            self, global_controller, base_path
    ):
        # remove_all deletes the base directory from disk
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_all()
        assert base_path.exists()

        await global_controller.remove_all()
        assert not base_path.exists()


class TestGlobalSessionControllerCallbackRegistration:
    def test_init_without_runner(self):
        # GlobalSessionController init does not crash when Runner is unavailable
        GlobalSessionController._instances = {}
        ctrl = GlobalSessionController()
        assert ctrl is not None


class TestGlobalSessionControllerSecurity:
    @pytest.mark.asyncio
    async def test_different_direct_subjects_isolated(
            self, global_controller, base_path
    ):
        # Sessions for different users are isolated
        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={"key": "value"})
        mock_session.update_state = MagicMock()
        mock_container = AgentSessionContainer(mock_session)

        with patch.object(DataContainerFactory, "create", return_value=mock_container):
            _, s1 = await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user1", session_id="session-u1"
            )
            _, s2 = await GlobalSessionController.create_direct_session(
                agent_id="agent1", user_id="user2", session_id="session-u2"
            )

        assert s1.session_scope != s2.session_scope
        assert s1.can_see("agent1", "session-u2") is False
        assert s2.can_see("agent1", "session-u1") is False

    @pytest.mark.asyncio
    async def test_downstream_unidirectional(
            self, global_controller, base_path
    ):
        # Downstream visibility is one-directional
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        main_scope = SessionScope(scope=MainScope())

        _, s1 = await ctrl1.create_if_not_exists(main_scope, "session-1")
        _, s2 = await ctrl2.create_if_not_exists(main_scope, "session-2")

        s1.add_downstream("agent2", "session-2", SharingPolicy(permission=Permission.READ))

        assert s1.can_see("agent2", "session-2") is True
        assert s2.can_see("agent1", "session-1") is False

    @pytest.mark.asyncio
    async def test_remove_session_complete_cleanup(
            self, global_controller, base_path
    ):
        # remove_agent deletes all session data from disk
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_all()

        agent_dir = base_path / "agent1"
        assert agent_dir.exists()

        await global_controller.remove_agent("agent1")
        assert not agent_dir.exists()


class TestGlobalSessionControllerCompatibility:
    @pytest.mark.asyncio
    async def test_load_old_meta_without_container_type(
            self, global_controller, base_path
    ):
        # Loading sessions.json without data_container_type uses default
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_all()

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for scope_data in data.values():
            for session_data in scope_data.get("sessions", []):
                if "data_container_type" in session_data:
                    del session_data["data_container_type"]
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        mock_session = MagicMock()
        mock_session.get_state = MagicMock(return_value={})
        mock_session.update_state = MagicMock()

        async def _mock_load(agent_id, session_id, serialized):
            return AgentSessionContainer(mock_session)

        global_controller.controllers.clear()
        with patch.object(AgentSessionContainer, "load", _mock_load):
            await global_controller.load_agent("agent1")
        ctrl = global_controller.get_agent("agent1")
        assert ctrl is not None


class TestGlobalSessionControllerResilience:
    @pytest.mark.asyncio
    async def test_flush_all_partial_failure(
            self, global_controller, base_path
    ):
        # flush_all does not crash when one agent fails
        _, ctrl1 = await global_controller.create_if_not_exist_agent("agent1")
        _, ctrl2 = await global_controller.create_if_not_exist_agent("agent2")
        main_scope = SessionScope(scope=MainScope())
        await ctrl1.create_if_not_exists(main_scope, "session-1")
        await ctrl2.create_if_not_exists(main_scope, "session-2")

        with patch.object(ctrl2, "flush", return_value=False):
            await global_controller.flush_all()

        meta1 = base_path / "agent1" / "sessions" / "sessions.json"
        assert meta1.exists()

    @pytest.mark.asyncio
    async def test_repeated_remove_same_agent(
            self, global_controller, base_path
    ):
        # Removing the same agent twice returns False the second time
        await global_controller.create_if_not_exist_agent("agent1")
        result1 = await global_controller.remove_agent("agent1")
        result2 = await global_controller.remove_agent("agent1")
        assert result1 is True
        assert result2 is False

    @pytest.mark.asyncio
    async def test_sessions_json_deleted_externally(
            self, global_controller, base_path
    ):
        # load_agent handles missing sessions.json gracefully
        _, ctrl = await global_controller.create_if_not_exist_agent("agent1")
        main_scope = SessionScope(scope=MainScope())
        await ctrl.create_if_not_exists(main_scope, "session-1")
        await global_controller.flush_all()

        meta_file = base_path / "agent1" / "sessions" / "sessions.json"
        meta_file.unlink()

        global_controller.controllers.clear()
        await global_controller.load_agent("agent1")
        ctrl = global_controller.get_agent("agent1")
        assert ctrl is not None
