# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for WorkflowStorage.
"""

from unittest.mock import Mock

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.extensions.checkpointer.redis.storage import WorkflowStorage


@pytest.mark.asyncio
async def test_workflow_storage_save_and_recover(redis_store, mock_workflow_session):
    """Test save and recover workflow state."""
    storage = WorkflowStorage(redis_store)
    session, session_id, workflow_id = mock_workflow_session

    # Set state
    state_data = {"workflow_key": "workflow_value"}
    session.state().get_state = Mock(return_value=state_data)
    session.state().update = Mock()
    session.state().commit = Mock()

    # Save
    await storage.save(session)

    # Verify exists
    assert await storage.exists(session) is True

    # Recover
    new_session, _, _ = mock_workflow_session
    new_session._session_id = session_id
    new_session.set_workflow_id(workflow_id)
    new_session.state().get_state = Mock(return_value={})
    new_session.state().set_state = Mock()
    new_session.state().update = Mock()
    new_session.state().commit = Mock()

    inputs = InteractiveInput()
    await storage.recover(new_session, inputs)

    # Verify state was recovered
    new_session.state().set_state.assert_called()


@pytest.mark.asyncio
async def test_workflow_storage_save_with_updates(redis_store, mock_workflow_session):
    """Test save with updates."""
    storage = WorkflowStorage(redis_store)
    session, session_id, workflow_id = mock_workflow_session

    # Set state and updates
    state_data = {"key": "value"}
    updates_data = {"update_key": "update_value"}

    session.state().get_state = Mock(return_value=state_data)
    session.state().get_updates = Mock(return_value=updates_data)
    session.state().update = Mock()
    session.state().commit = Mock()

    # Save
    await storage.save(session)

    # Verify exists
    assert await storage.exists(session) is True


@pytest.mark.asyncio
async def test_workflow_storage_exists(redis_store, mock_workflow_session):
    """Test exists method."""
    storage = WorkflowStorage(redis_store)
    session, session_id, workflow_id = mock_workflow_session

    # Initially doesn't exist
    assert await storage.exists(session) is False

    # Save state
    session.state().get_state = Mock(return_value={"key": "value"})
    session.state().update = Mock()
    session.state().commit = Mock()
    await storage.save(session)

    # Now exists
    assert await storage.exists(session) is True


@pytest.mark.asyncio
async def test_workflow_storage_clear(redis_store, mock_workflow_session):
    """Test clear method."""
    storage = WorkflowStorage(redis_store)
    session, session_id, workflow_id = mock_workflow_session

    # Save state
    session.state().get_state = Mock(return_value={"key": "value"})
    session.state().update = Mock()
    session.state().commit = Mock()
    await storage.save(session)
    assert await storage.exists(session) is True

    # Clear
    await storage.clear(workflow_id, session_id)

    # Verify cleared
    assert await storage.exists(session) is False
