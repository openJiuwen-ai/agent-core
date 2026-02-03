# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for RedisCheckpointer core functionality.
"""

import uuid
from unittest.mock import Mock

import pytest

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.graph.pregel import TASK_STATUS_INTERRUPT
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.internal.workflow import WorkflowSession
from openjiuwen.core.session.state.agent_state import StateCollection
from openjiuwen.extensions.checkpointer.redis.checkpointer import RedisCheckpointer
from openjiuwen.extensions.checkpointer.redis.storage import GraphStore
from openjiuwen.extensions.store.kv.redis_store import RedisStore


@pytest.mark.asyncio
async def test_checkpointer_pre_agent_execute(checkpointer, mock_agent_session):
    """Test pre_agent_execute saves and recovers agent state."""
    session, session_id, agent_id = mock_agent_session

    # Set some state using update method
    session.state().update({"key1": "value1", "key2": 42})

    # Save state
    await checkpointer.pre_agent_execute(session, None)
    await checkpointer.interrupt_agent_execute(session)

    # Create new session and recover
    new_session, _, _ = mock_agent_session
    new_session._session_id = session_id
    new_session.agent_id = Mock(return_value=agent_id)
    new_state = StateCollection()
    new_session.state = Mock(return_value=new_state)

    await checkpointer.pre_agent_execute(new_session, None)

    # Verify state was recovered using get method
    assert new_session.state().get("key1") == "value1"
    assert new_session.state().get("key2") == 42


@pytest.mark.asyncio
async def test_checkpointer_pre_agent_execute_with_inputs(checkpointer, mock_agent_session):
    """Test pre_agent_execute with inputs."""
    session, session_id, agent_id = mock_agent_session

    inputs = {"test_input": "test_value"}
    await checkpointer.pre_agent_execute(session, inputs)

    # Verify inputs were set - INTERACTIVE_INPUT is set in agent_state
    state = session.state().get_state()
    agent_state = state.get("agent_state", {})
    assert INTERACTIVE_INPUT in agent_state
    assert agent_state[INTERACTIVE_INPUT] == [inputs]


@pytest.mark.asyncio
async def test_checkpointer_interrupt_agent_execute(checkpointer, mock_agent_session):
    """Test interrupt_agent_execute saves state."""
    session, session_id, agent_id = mock_agent_session

    # Set state using update method
    session.state().update({"key": "value"})

    await checkpointer.interrupt_agent_execute(session)

    # Verify state was saved by checking exists
    assert await checkpointer._agent_storage.exists(session) is True


@pytest.mark.asyncio
async def test_checkpointer_post_agent_execute(checkpointer, mock_agent_session):
    """Test post_agent_execute saves state."""
    session, session_id, agent_id = mock_agent_session

    # Set state using update method
    session.state().update({"key": "value"})

    await checkpointer.post_agent_execute(session)

    # Verify state was saved
    assert await checkpointer._agent_storage.exists(session) is True


@pytest.mark.asyncio
async def test_checkpointer_pre_workflow_execute(checkpointer, mock_agent_session):
    """Test pre_workflow_execute recovers workflow state."""
    agent_session, session_id, agent_id = mock_agent_session
    workflow_id = f"test_workflow_{uuid.uuid4().hex[:8]}"

    # Create workflow session
    session = WorkflowSession(
        workflow_id=workflow_id,
        parent=agent_session,
        session_id=session_id,
    )

    # Set some state
    state_data = {"workflow_key": "workflow_value"}
    session.state().get_state = Mock(return_value=state_data)
    session.state().update = Mock()
    session.state().commit = Mock()

    # Save state
    await checkpointer._workflow_storage.save(session)

    # Create new session and recover
    new_agent_session, _, _ = mock_agent_session
    new_agent_session._session_id = session_id
    new_session = WorkflowSession(
        workflow_id=workflow_id,
        parent=new_agent_session,
        session_id=session_id,
    )
    new_session.state().get_state = Mock(return_value={})
    new_session.state().set_state = Mock()
    new_session.state().update = Mock()
    new_session.state().commit = Mock()

    inputs = InteractiveInput()
    await checkpointer.pre_workflow_execute(new_session, inputs)

    # Verify state was recovered
    new_session.state().set_state.assert_called()


@pytest.mark.asyncio
async def test_checkpointer_post_workflow_execute_success(checkpointer, mock_workflow_session):
    """Test post_workflow_execute clears state on success."""
    session, session_id, workflow_id = mock_workflow_session

    # Set some state
    state_data = {"key": "value"}
    session.state().get_state = Mock(return_value=state_data)
    session.state().update = Mock()
    session.state().commit = Mock()

    # Save state
    await checkpointer._workflow_storage.save(session)

    # Execute post_workflow_execute with success result
    result = {}
    await checkpointer.post_workflow_execute(session, result, None)

    # Verify state was cleared
    assert await checkpointer._workflow_storage.exists(session) is False


@pytest.mark.asyncio
async def test_checkpointer_post_workflow_execute_with_exception(checkpointer, mock_workflow_session):
    """Test post_workflow_execute saves state and raises exception."""
    session, session_id, workflow_id = mock_workflow_session

    state_data = {"key": "value"}
    session.state().get_state = Mock(return_value=state_data)
    session.state().update = Mock()
    session.state().commit = Mock()

    # Execute post_workflow_execute with exception
    exception = ValueError("Test error")

    with pytest.raises(ValueError, match="Test error"):
        await checkpointer.post_workflow_execute(session, {}, exception)

    # Verify state was saved
    assert await checkpointer._workflow_storage.exists(session) is True


@pytest.mark.asyncio
async def test_checkpointer_post_workflow_execute_with_interrupt(checkpointer, mock_workflow_session):
    """Test post_workflow_execute saves state on interrupt."""
    session, session_id, workflow_id = mock_workflow_session

    state_data = {"key": "value"}
    session.state().get_state = Mock(return_value=state_data)
    session.state().update = Mock()
    session.state().commit = Mock()

    # Execute post_workflow_execute with interrupt
    result = {TASK_STATUS_INTERRUPT: True}
    await checkpointer.post_workflow_execute(session, result, None)

    # Verify state was saved
    assert await checkpointer._workflow_storage.exists(session) is True


@pytest.mark.asyncio
async def test_checkpointer_release_with_agent_id(checkpointer, mock_agent_session):
    """Test release with specific agent_id."""
    session, session_id, agent_id = mock_agent_session

    # Save some state using update method
    session.state().update({"key": "value"})
    await checkpointer.interrupt_agent_execute(session)

    # Release agent
    await checkpointer.release(session_id, agent_id)

    # Verify state was cleared
    assert await checkpointer._agent_storage.exists(session) is False


@pytest.mark.asyncio
async def test_checkpointer_release_without_agent_id(checkpointer, redis_client):
    """Test release without agent_id deletes all session keys."""
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"

    # Create some keys with the session_id pattern
    await redis_client.set(f"{session_id}:key1", "value1")
    await redis_client.set(f"{session_id}:key2", "value2")
    await redis_client.set(f"other_session:key3", "value3")

    # Release session
    await checkpointer.release(session_id)

    # Verify session keys were deleted
    assert await redis_client.exists(f"{session_id}:key1") == 0
    assert await redis_client.exists(f"{session_id}:key2") == 0
    # Verify other session keys still exist
    assert await redis_client.exists(f"other_session:key3") == 1

    # Cleanup
    await redis_client.delete("other_session:key3")


@pytest.mark.asyncio
async def test_checkpointer_release_with_none_redis_store():
    """Test release when redis_store is None."""
    # Create a mock RedisStore with None redis
    mock_redis_store = Mock(spec=RedisStore)
    mock_redis_store.redis = None
    mock_redis_store.delete_keys_by_pattern = Mock()

    checkpointer = RedisCheckpointer(redis_store=mock_redis_store)
    # Should not raise error
    await checkpointer.release("session_id", "agent_id")


@pytest.mark.asyncio
async def test_checkpointer_graph_store(checkpointer):
    """Test graph_store method returns GraphStore instance."""
    graph_store = checkpointer.graph_store()
    assert isinstance(graph_store, GraphStore)


@pytest.mark.asyncio
@pytest.mark.skip(reason="Skip by default - this test flushes all Redis data")
async def test_flush_all_redis_keys(redis_client):
    """Test deleting all key-value data in current Redis database."""
    # Create some test keys with different prefixes
    test_keys = [
        "test_session_1:agent_state",
        "test_session_2:workflow_state",
        "test_session_3:graph_state",
        "other_key_1",
        "other_key_2",
    ]

    # Set test data
    for key in test_keys:
        await redis_client.set(key, f"value_{key}")

    # Verify all keys exist
    for key in test_keys:
        assert await redis_client.exists(key) == 1

    # Delete all keys in current database
    await redis_client.flushdb()

    # Verify all keys are deleted
    for key in test_keys:
        assert await redis_client.exists(key) == 0

    # Verify database is empty
    keys_count = await redis_client.dbsize()
    assert keys_count == 0
