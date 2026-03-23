# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for AgentStorage.
"""

import asyncio
from unittest.mock import Mock

import pytest

from openjiuwen.core.session.checkpointer import (
    build_key_with_namespace,
    SESSION_NAMESPACE_AGENT,
    SESSION_NAMESPACE_AGENT_TEAM,
)
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent_team import AgentTeamSession
from openjiuwen.core.session.state.agent_state import StateCollection
from openjiuwen.extensions.checkpointer.redis.storage import (
    AgentGroupStorage,
    AgentStorage,
)
from openjiuwen.extensions.store.kv.redis_store import RedisStore


@pytest.mark.asyncio
async def test_agent_storage_save_and_recover(redis_store, mock_agent_session):
    """Test save and recover agent state."""
    storage = AgentStorage(redis_store)
    session, session_id, agent_id = mock_agent_session

    # Set state using update method
    session.state().update({"key1": "value1", "key2": 42, "list": [1, 2, 3]})

    # Save
    await storage.save(session)

    # Verify exists
    assert await storage.exists(session) is True

    # Recover to new session
    new_session, _, _ = mock_agent_session
    new_session._session_id = session_id
    new_session.agent_id = Mock(return_value=agent_id)
    new_state = StateCollection()
    new_session.state = Mock(return_value=new_state)

    await storage.recover(new_session)

    # Verify state was recovered using get method
    assert new_session.state().get("key1") == "value1"
    assert new_session.state().get("key2") == 42
    assert new_session.state().get("list") == [1, 2, 3]


@pytest.mark.asyncio
async def test_agent_storage_save_empty_state(redis_store, mock_agent_session):
    """Test save with empty state."""
    storage = AgentStorage(redis_store)
    session, session_id, agent_id = mock_agent_session

    # Save empty state (no update needed, state is already empty)
    await storage.save(session)

    # Verify exists
    assert await storage.exists(session) is True


@pytest.mark.asyncio
async def test_agent_storage_recover_nonexistent_state(redis_store, mock_agent_session):
    """Test recover when state doesn't exist."""
    storage = AgentStorage(redis_store)
    session, session_id, agent_id = mock_agent_session

    # Recover non-existent state (should not raise error)
    await storage.recover(session)

    # Verify state is still empty
    state = session.state().get_state()
    assert state.get("global_state") == {}
    assert state.get("agent_state") == {}


@pytest.mark.asyncio
async def test_agent_storage_exists(redis_store, mock_agent_session):
    """Test exists method."""
    storage = AgentStorage(redis_store)
    session, session_id, agent_id = mock_agent_session

    # Initially doesn't exist
    assert await storage.exists(session) is False

    # Save state using update method
    session.state().update({"key": "value"})
    await storage.save(session)

    # Now exists
    assert await storage.exists(session) is True


@pytest.mark.asyncio
async def test_agent_storage_clear(redis_store, mock_agent_session):
    """Test clear method."""
    storage = AgentStorage(redis_store)
    session, session_id, agent_id = mock_agent_session

    # Save state using update method
    session.state().update({"key": "value"})
    await storage.save(session)
    assert await storage.exists(session) is True

    # Clear
    await storage.clear(agent_id, session_id)

    # Verify cleared
    assert await storage.exists(session) is False


@pytest.mark.asyncio
async def test_agent_storage_ttl_refresh_on_read(redis_client, mock_agent_session):
    """Test TTL refresh on read."""
    ttl = {
        "default_ttl": 1,  # 1 minute = 60 seconds
        "refresh_on_read": True,
    }
    redis_store = RedisStore(redis_client)
    storage = AgentStorage(redis_store, ttl=ttl)
    session, session_id, agent_id = mock_agent_session

    # Save state using update method
    session.state().update({"key": "value"})
    await storage.save(session)

    # Get TTL before read
    dump_type_key = build_key_with_namespace(session_id, SESSION_NAMESPACE_AGENT, agent_id,
                                             storage._STATE_BLOBS_DUMP_TYPE)
    ttl_before = await redis_client.ttl(dump_type_key)

    # Wait a bit
    await asyncio.sleep(0.1)

    # Recover (read) - should refresh TTL
    await storage.recover(session)

    # Get TTL after read
    ttl_after = await redis_client.ttl(dump_type_key)

    # TTL should be refreshed (approximately 60 seconds)
    assert ttl_after >= 59  # Allow 1 second tolerance


@pytest.mark.asyncio
async def test_agent_group_storage_save_and_recover(redis_store):
    """Test save and recover agent team state."""
    storage = AgentGroupStorage(redis_store)
    session = AgentTeamSession(session_id="group_session", team_id="group_a", config=Config())
    session.state().update({"agent_local": "ignored"})
    session.state().update_global({"shared_key": "shared_value", "counter": 3})

    await storage.save(session)
    assert await storage.exists(session) is True

    recovered = AgentTeamSession(session_id="group_session", team_id="group_a", config=Config())
    recovered.state = Mock(return_value=StateCollection())

    await storage.recover(recovered)

    assert recovered.state().get_global("shared_key") == "shared_value"
    assert recovered.state().get_global("counter") == 3
    assert recovered.state().get("agent_local") is None


@pytest.mark.asyncio
async def test_agent_group_storage_clear(redis_store):
    """Test clear method for agent team state."""
    storage = AgentGroupStorage(redis_store)
    session = AgentTeamSession(session_id="group_session", team_id="group_a", config=Config())
    session.state().update_global({"shared_key": "shared_value"})

    await storage.save(session)
    assert await storage.exists(session) is True

    await storage.clear("group_a", "group_session")

    assert await storage.exists(session) is False


@pytest.mark.asyncio
async def test_agent_group_storage_ttl_refresh_on_read(redis_client):
    """Test TTL refresh on read for agent team state."""
    ttl = {
        "default_ttl": 1,
        "refresh_on_read": True,
    }
    redis_store = RedisStore(redis_client)
    storage = AgentGroupStorage(redis_store, ttl=ttl)
    session = AgentTeamSession(session_id="group_session", team_id="group_a", config=Config())
    session.state().update_global({"shared_key": "shared_value"})

    await storage.save(session)

    dump_type_key = build_key_with_namespace(
        "group_session",
        SESSION_NAMESPACE_AGENT_TEAM,
        "group_a",
        storage._state_dump_type_key,
    )
    ttl_before = await redis_client.ttl(dump_type_key)

    await asyncio.sleep(0.1)
    await storage.recover(session)

    ttl_after = await redis_client.ttl(dump_type_key)

    assert ttl_before > 0
    assert ttl_after >= 59
