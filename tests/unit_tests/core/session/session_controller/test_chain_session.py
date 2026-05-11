# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json

import pytest

from openjiuwen.core.session.session_controller.chain_session import ChainSession
from openjiuwen.core.session.session_controller.data_container import (
    Permission,
    SharingPolicy,
    AgentSessionContainer,
)
from openjiuwen.core.session.session_controller.schema import SessionMeta
from openjiuwen.core.session.session_controller.scope import (
    MainScope,
    DirectSubject,
    SessionScope,
)


@pytest.fixture
def tmp_session_dir(tmp_path):
    return tmp_path / "test_session"


@pytest.fixture
def session_scope():
    return SessionScope(scope=MainScope(), subject=DirectSubject("user1"))


from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_agent_session():
    session = MagicMock()
    state = {"key": "value"}
    session.get_state = MagicMock(return_value=state)
    session.update_state = MagicMock(side_effect=lambda d: state.update(d))
    session.dump_state = MagicMock(return_value=state)
    return session


@pytest.fixture
def chain_session(session_scope, tmp_session_dir, mock_agent_session):
    container = AgentSessionContainer(mock_agent_session)
    session = ChainSession(
        agent_id="agent1",
        session_scope=session_scope,
        session_id="session-1",
        data_container=container,
        session_dir=tmp_session_dir,
    )
    session.update_from_meta(SessionMeta.create_new("session-1"))
    return session


class TestChainSessionBasic:
    def test_session_key(self, chain_session, session_scope):
        # session_key returns a SessionScopeKey with the correct agent_id and scope
        key = chain_session.session_key
        assert key.agent_id == "agent1"
        assert key.session_scope == session_scope

    def test_properties(self, chain_session):
        # Initial metadata properties are set correctly
        assert chain_session.created_at > 0
        assert chain_session.updated_at > 0
        assert chain_session.version == 1
        assert chain_session.is_active is True

    def test_is_active_setter(self, chain_session):
        # is_active can be toggled on and off
        chain_session.is_active = False
        assert chain_session.is_active is False
        chain_session.is_active = True
        assert chain_session.is_active is True


class TestChainSessionData:
    def test_get_data(self, chain_session):
        # get_data returns the current data from the container
        data = chain_session.get_data()
        assert data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_update_data(self, chain_session):
        # update_data merges the given dict and increments the version
        result = await chain_session.update_data({"count": 1})
        assert result is True
        assert chain_session.get_data()["count"] == 1
        assert chain_session.version == 2

    @pytest.mark.asyncio
    async def test_update_data_failure(self, chain_session, mock_agent_session):
        mock_agent_session.update_state = MagicMock(side_effect=RuntimeError("fail"))
        result = await chain_session.update_data({"count": 1})
        assert result is False
        assert chain_session.version == 1


class TestChainSessionDownstream:
    def test_add_downstream(self, chain_session):
        # add_downstream registers a downstream relationship
        policy = SharingPolicy(permission=Permission.READ)
        chain_session.add_downstream("agent2", "session-2", policy)
        assert chain_session.has_downstream("agent2", "session-2")

    def test_remove_downstream(self, chain_session):
        # remove_downstream deletes a downstream relationship
        chain_session.add_downstream("agent2", "session-2")
        chain_session.remove_downstream("agent2", "session-2")
        assert not chain_session.has_downstream("agent2", "session-2")

    def test_remove_nonexistent_downstream(self, chain_session):
        # Removing a non-existent downstream is a no-op
        chain_session.remove_downstream("agent2", "session-2")

    def test_get_downstreams(self, chain_session):
        # get_downstreams returns a mapping of (agent, session) -> policy
        policy = SharingPolicy(permission=Permission.READ)
        chain_session.add_downstream("agent2", "session-2", policy)
        downstreams = chain_session.get_downstreams()
        assert ("agent2", "session-2") in downstreams
        assert downstreams[("agent2", "session-2")] == policy

    def test_get_downstreams_returns_copy(self, chain_session):
        # get_downstreams returns a copy; mutating it does not affect the session
        chain_session.add_downstream("agent2", "session-2")
        downstreams = chain_session.get_downstreams()
        downstreams.clear()
        assert chain_session.has_downstream("agent2", "session-2")

    def test_get_downstream_policy(self, chain_session):
        # get_downstream_policy returns the policy for a specific downstream
        policy = SharingPolicy(permission=Permission.READ, field_scopes={"field1"})
        chain_session.add_downstream("agent2", "session-2", policy)
        found = chain_session.get_downstream_policy("agent2", "session-2")
        assert found == policy

    def test_get_downstream_policy_not_found(self, chain_session):
        # get_downstream_policy returns None for a non-existent downstream
        assert chain_session.get_downstream_policy("agent2", "session-2") is None

    def test_remove_all_downstreams(self, chain_session):
        # remove_all_downstreams clears every downstream relationship
        chain_session.add_downstream("agent2", "session-2")
        chain_session.add_downstream("agent3", "session-3")
        chain_session.remove_all_downstreams()
        assert not chain_session.has_downstream("agent2", "session-2")
        assert not chain_session.has_downstream("agent3", "session-3")


class TestChainSessionVisibility:
    def test_can_see_self(self, chain_session):
        # A session can always see itself
        assert chain_session.can_see("agent1", "session-1") is True

    def test_can_see_downstream(self, chain_session):
        # A session can see its downstream targets
        chain_session.add_downstream("agent2", "session-2")
        assert chain_session.can_see("agent2", "session-2") is True

    def test_cannot_see_unknown(self, chain_session):
        # A session cannot see targets that are not downstream
        assert chain_session.can_see("agent2", "session-2") is False


class TestChainSessionPersistence:
    @pytest.mark.asyncio
    async def test_flush_and_load(self, chain_session, tmp_session_dir):
        # flush writes state.data and .link files to disk
        chain_session.add_downstream(
            "agent2", "session-2",
            SharingPolicy(permission=Permission.READ, field_scopes={"field1"})
        )
        await chain_session.flush()

        state_file = tmp_session_dir / "state.data"
        assert state_file.exists()

        with open(state_file, "r", encoding="utf-8") as f:
            state_data = json.load(f)
        assert state_data["data"] == {}
        assert state_data["meta"]["is_active"] is True

        link_file = tmp_session_dir / "downstreams" / "agent2_session-2.link"
        assert link_file.exists()

    @pytest.mark.asyncio
    async def test_flush_removes_deleted_downstream(
            self, chain_session, tmp_session_dir
    ):
        # flush deletes .link files for removed downstreams
        chain_session.add_downstream("agent2", "session-2")
        await chain_session.flush()
        link_file = tmp_session_dir / "downstreams" / "agent2_session-2.link"
        assert link_file.exists()

        chain_session.remove_downstream("agent2", "session-2")
        await chain_session.flush()
        assert not link_file.exists()

    @pytest.mark.asyncio
    async def test_load(self, chain_session, tmp_session_dir, session_scope, mock_agent_session):
        # load restores session data and downstream relationships from disk
        chain_session.add_downstream(
            "agent2", "session-2",
            SharingPolicy(permission=Permission.READ)
        )
        await chain_session.flush()

        new_container = AgentSessionContainer(mock_agent_session)
        new_session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=new_container,
            session_dir=tmp_session_dir,
        )
        with patch.object(
                AgentSessionContainer, "load",
                return_value=AgentSessionContainer(mock_agent_session),
        ):
            result = await new_session.load()
        assert result is True
        assert new_session.is_active is True
        assert new_session.has_downstream("agent2", "session-2")

    @pytest.mark.asyncio
    async def test_load_skips_removed_link(
            self, chain_session, tmp_session_dir, session_scope, mock_agent_session
    ):
        # load skips .link files marked as removed and deletes them
        downstreams_dir = tmp_session_dir / "downstreams"
        downstreams_dir.mkdir(parents=True, exist_ok=True)
        link_file = downstreams_dir / "agent2_session-2.link"
        link_file.write_text(json.dumps({"removed": True}), encoding="utf-8")

        new_session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=AgentSessionContainer(mock_agent_session),
            session_dir=tmp_session_dir,
        )
        new_session.update_from_meta(SessionMeta.create_new("session-1"))
        with patch.object(
                AgentSessionContainer, "load",
                return_value=AgentSessionContainer(mock_agent_session),
        ):
            await new_session.load()
        assert not new_session.has_downstream("agent2", "session-2")
        assert not link_file.exists()


class TestChainSessionMeta:
    def test_to_session_meta(self, chain_session):
        # to_session_meta converts the session into a SessionMeta dataclass
        meta = chain_session.to_session_meta()
        assert meta.session_id == "session-1"
        assert meta.is_active is True

    def test_update_from_meta(self, chain_session):
        # update_from_meta applies metadata fields to the session
        meta = SessionMeta.create_new("session-1")
        meta.version = 10
        meta.is_active = False
        chain_session.update_from_meta(meta)
        assert chain_session.version == 10
        assert chain_session.is_active is False
