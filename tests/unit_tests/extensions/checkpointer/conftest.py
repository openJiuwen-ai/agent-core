# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Shared fixtures for checkpointer tests.
"""

import uuid
from unittest.mock import Mock

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.internal.workflow import WorkflowSession
from openjiuwen.extensions.checkpointer.redis.checkpointer import RedisCheckpointer
from openjiuwen.extensions.store.kv.redis_store import RedisStore

pytest.skip(
    reason="need redis local environment to run",
    allow_module_level=True,
)


@pytest_asyncio.fixture
async def redis_client():
    """Create a Redis client for testing."""
    redis = Redis.from_url("redis://127.0.0.1:6379", decode_responses=False)
    yield redis
    # Cleanup: flush all test data
    await redis.flushdb()
    await redis.aclose()


@pytest_asyncio.fixture
async def redis_store(redis_client):
    """Create a RedisStore instance for testing."""
    return RedisStore(redis_client)


@pytest_asyncio.fixture
async def checkpointer(redis_store):
    """Create a RedisCheckpointer instance for testing."""
    return RedisCheckpointer(redis_store)


@pytest_asyncio.fixture
async def checkpointer_with_ttl(redis_store):
    """Create a RedisCheckpointer instance with TTL enabled."""
    ttl = {
        "default_ttl": 10,  # 10 minutes
        "refresh_on_read": True,
    }
    return RedisCheckpointer(redis_store, ttl=ttl)


@pytest_asyncio.fixture
def mock_agent_session():
    """Create a mock AgentSession for testing."""
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    agent_id = f"test_agent_{uuid.uuid4().hex[:8]}"

    session = AgentSession(session_id=session_id, config=Config())
    # Mock agent_id method
    session.agent_id = Mock(return_value=agent_id)

    return session, session_id, agent_id


@pytest_asyncio.fixture
def mock_workflow_session(mock_agent_session):
    """Create a mock WorkflowSession for testing."""
    agent_session, session_id, agent_id = mock_agent_session
    workflow_id = f"test_workflow_{uuid.uuid4().hex[:8]}"

    workflow_session = WorkflowSession(
        workflow_id=workflow_id,
        parent=agent_session,
        session_id=session_id,
    )

    return workflow_session, session_id, workflow_id
