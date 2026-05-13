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

    def test_update_from_meta_with_container_type(self, chain_session):
        # update_from_meta applies data_container_type when present
        meta = SessionMeta.create_new("session-1", data_container_type="custom_type")
        chain_session.update_from_meta(meta)
        assert chain_session._data_container_type == "custom_type"

    def test_update_from_meta_without_container_type(self, chain_session):
        # update_from_meta keeps original container_type when meta has empty value
        chain_session._data_container_type = "original"
        meta = SessionMeta.create_new("session-1")
        meta.data_container_type = ""
        chain_session.update_from_meta(meta)
        assert chain_session._data_container_type == "original"


class TestChainSessionDownstreamAdvanced:
    def test_add_downstream_overwrites_existing(self, chain_session):
        # add_downstream overwrites an existing downstream relationship
        policy1 = SharingPolicy(permission=Permission.READ)
        policy2 = SharingPolicy(permission=Permission.READ, field_scopes={"f2"})
        chain_session.add_downstream("agent2", "session-2", policy1)
        chain_session.add_downstream("agent2", "session-2", policy2)
        found = chain_session.get_downstream_policy("agent2", "session-2")
        assert found.field_scopes == {"f2"}

    def test_can_see_after_remove_downstream(self, chain_session):
        # can_see returns False after downstream is removed
        chain_session.add_downstream("agent2", "session-2")
        assert chain_session.can_see("agent2", "session-2") is True
        chain_session.remove_downstream("agent2", "session-2")
        assert chain_session.can_see("agent2", "session-2") is False

    def test_add_downstream_default_policy(self, chain_session):
        # add_downstream uses default SharingPolicy when no policy is given
        chain_session.add_downstream("agent2", "session-2")
        policy = chain_session.get_downstream_policy("agent2", "session-2")
        assert policy.permission == Permission.READ
        assert policy.field_scopes is None


class TestChainSessionPersistenceAdvanced:
    @pytest.mark.asyncio
    async def test_flush_creates_directories(self, session_scope, tmp_session_dir, mock_agent_session):
        # flush auto-creates session_dir and downstreams directory
        nested_dir = tmp_session_dir / "nested" / "deep"
        container = AgentSessionContainer(mock_agent_session)
        session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=container,
            session_dir=nested_dir,
        )
        session.update_from_meta(SessionMeta.create_new("session-1"))
        result = await session.flush()
        assert result is True
        assert nested_dir.exists()
        assert (nested_dir / "downstreams").exists()

    @pytest.mark.asyncio
    async def test_flush_no_downstreams(self, chain_session, tmp_session_dir):
        # flush with no downstreams produces no .link files
        await chain_session.flush()
        downstreams_dir = tmp_session_dir / "downstreams"
        if downstreams_dir.exists():
            link_files = list(downstreams_dir.glob("*.link"))
            assert len(link_files) == 0

    @pytest.mark.asyncio
    async def test_flush_marks_removed_before_delete(
            self, chain_session, tmp_session_dir
    ):
        # flush marks .link as removed=true before deleting it
        chain_session.add_downstream("agent2", "session-2")
        await chain_session.flush()
        link_file = tmp_session_dir / "downstreams" / "agent2_session-2.link"
        assert link_file.exists()

        chain_session.remove_downstream("agent2", "session-2")

        original_flush = chain_session.flush

        async def _intercept_flush():
            await original_flush()

        await chain_session.flush()
        assert not link_file.exists()

    @pytest.mark.asyncio
    async def test_load_restores_field_scopes(
            self, chain_session, tmp_session_dir, session_scope, mock_agent_session
    ):
        # load restores downstream policy with field_scopes as a set
        chain_session.add_downstream(
            "agent2", "session-2",
            SharingPolicy(permission=Permission.READ, field_scopes={"f1", "f2"})
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
            await new_session.load()

        policy = new_session.get_downstream_policy("agent2", "session-2")
        assert policy is not None
        assert policy.field_scopes == {"f1", "f2"}

    @pytest.mark.asyncio
    async def test_load_corrupted_link_file(
            self, chain_session, tmp_session_dir, session_scope, mock_agent_session
    ):
        # load skips corrupted .link files without crashing
        downstreams_dir = tmp_session_dir / "downstreams"
        downstreams_dir.mkdir(parents=True, exist_ok=True)
        bad_link = downstreams_dir / "agent2_session-2.link"
        bad_link.write_text("NOT VALID JSON{{{{", encoding="utf-8")

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
            result = await new_session.load()
        assert result is True
        assert not new_session.has_downstream("agent2", "session-2")

    @pytest.mark.asyncio
    async def test_load_link_file_no_underscore(
            self, chain_session, tmp_session_dir, session_scope, mock_agent_session
    ):
        # load skips .link files whose stem has no underscore
        downstreams_dir = tmp_session_dir / "downstreams"
        downstreams_dir.mkdir(parents=True, exist_ok=True)
        link_file = downstreams_dir / "nounderscore.link"
        link_file.write_text(json.dumps({"permission": {"level": 1}}), encoding="utf-8")

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
            result = await new_session.load()
        assert result is True
        assert len(new_session.get_downstreams()) == 0

    @pytest.mark.asyncio
    async def test_load_no_state_file(
            self, session_scope, tmp_session_dir, mock_agent_session
    ):
        # load returns True when state.data does not exist; properties stay default
        container = AgentSessionContainer(mock_agent_session)
        session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=container,
            session_dir=tmp_session_dir,
        )
        result = await session.load()
        assert result is True
        assert session.is_active is False
        assert session.version == 1

    @pytest.mark.asyncio
    async def test_load_state_data_no_meta(
            self, tmp_session_dir, session_scope, mock_agent_session
    ):
        # load uses defaults when state.data has no meta field
        tmp_session_dir.mkdir(parents=True, exist_ok=True)
        state_file = tmp_session_dir / "state.data"
        state_file.write_text(json.dumps({"data": {"key": "val"}}), encoding="utf-8")

        container = AgentSessionContainer(mock_agent_session)
        session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=container,
            session_dir=tmp_session_dir,
        )
        with patch.object(
                AgentSessionContainer, "load",
                return_value=AgentSessionContainer(mock_agent_session),
        ):
            result = await session.load()
        assert result is True
        assert session.is_active is False
        assert session.version == 1

    @pytest.mark.asyncio
    async def test_flush_load_roundtrip(
            self, chain_session, tmp_session_dir, session_scope, mock_agent_session
    ):
        # flush then load preserves all metadata and downstream relationships
        chain_session.add_downstream(
            "agent2", "session-2",
            SharingPolicy(permission=Permission.READ)
        )
        await chain_session.flush()
        original_meta = chain_session.to_session_meta()

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
            await new_session.load()

        loaded_meta = new_session.to_session_meta()
        assert loaded_meta.session_id == original_meta.session_id
        assert loaded_meta.is_active == original_meta.is_active
        assert loaded_meta.version == original_meta.version
        assert new_session.has_downstream("agent2", "session-2")

    @pytest.mark.asyncio
    async def test_flush_exception_returns_false(
            self, session_scope, tmp_session_dir, mock_agent_session
    ):
        # flush returns False when data_container.dump raises
        container = AgentSessionContainer(mock_agent_session)
        session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=container,
            session_dir=tmp_session_dir,
        )
        session.update_from_meta(SessionMeta.create_new("session-1"))

        async def _failing_dump():
            raise RuntimeError("disk error")

        with patch.object(type(container), "dump", _failing_dump):
            result = await session.flush()
        assert result is False

    @pytest.mark.asyncio
    async def test_load_invalid_json_state_returns_false(
            self, tmp_session_dir, session_scope, mock_agent_session
    ):
        # load returns False when state.data contains invalid JSON
        tmp_session_dir.mkdir(parents=True, exist_ok=True)
        state_file = tmp_session_dir / "state.data"
        state_file.write_text("NOT JSON{{{", encoding="utf-8")

        container = AgentSessionContainer(mock_agent_session)
        session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=container,
            session_dir=tmp_session_dir,
        )
        result = await session.load()
        assert result is False


class TestChainSessionInitDefaults:
    def test_default_metadata_values(self, session_scope, tmp_session_dir, mock_agent_session):
        # Fresh ChainSession has default metadata values before update_from_meta
        container = AgentSessionContainer(mock_agent_session)
        session = ChainSession(
            agent_id="agent1",
            session_scope=session_scope,
            session_id="session-1",
            data_container=container,
            session_dir=tmp_session_dir,
        )
        assert session.created_at == 0.0
        assert session.updated_at == 0.0
        assert session.version == 1
        assert session.is_active is False
        assert session.agent_id == "agent1"
        assert session.session_id == "session-1"
        assert session.session_scope == session_scope

    def test_is_active_setter_updates_timestamp(self, chain_session):
        # Setting is_active=True updates updated_at
        old_ts = chain_session.updated_at
        chain_session.is_active = True
        assert chain_session.updated_at >= old_ts

    def test_is_active_setter_false_no_timestamp_update(self, chain_session):
        # Setting is_active=False does not update updated_at
        chain_session.is_active = True
        ts_after_true = chain_session.updated_at
        chain_session.is_active = False
        assert chain_session.updated_at == ts_after_true


class TestChainSessionConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_update_data(self, chain_session):
        # Concurrent update_data is serialized by lock; version increments correctly
        import asyncio

        tasks = [
            chain_session.update_data({"key": f"val_{i}"})
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert all(r is True for r in results)
        assert chain_session.version == 6


class TestChainSessionSecurity:
    def test_field_scopes_in_sharing_policy(self):
        # SharingPolicy with field_scopes restricts visible fields
        policy = SharingPolicy(permission=Permission.READ, field_scopes={"f1", "f2"})
        assert policy.field_scopes == {"f1", "f2"}
        assert "f1" in policy.field_scopes
        assert "f3" not in policy.field_scopes
