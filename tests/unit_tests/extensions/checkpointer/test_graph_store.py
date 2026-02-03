# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for GraphStore.
"""

import asyncio
import uuid

import pytest

from openjiuwen.core.graph.pregel.base import Message
from openjiuwen.core.graph.store import (
    create_state,
    PendingNode,
)
from openjiuwen.core.session.checkpointer import (
    build_key_with_namespace,
    WORKFLOW_NAMESPACE_GRAPH,
)
from openjiuwen.extensions.checkpointer.redis.storage import (
    GraphStore,
)
from openjiuwen.extensions.store.kv.redis_store import RedisStore


@pytest.mark.asyncio
async def test_graph_store_save_and_get(redis_store):
    """Test save and get graph state."""
    store = GraphStore(redis_store)
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    ns = "test_namespace"

    # Create graph state
    graph_state = create_state(
        ns=ns,
        step=1,
        channel_snapshot={"ch1": 42, "ch2": "test_value"},
        pending_buffer=[],
        pending_node={},
        node_version={"node1": 1},
    )

    # Save
    await store.save(session_id, ns, graph_state)

    # Get
    loaded = await store.get(session_id, ns)

    assert loaded is not None
    assert loaded.ns == ns
    assert loaded.step == 1
    assert loaded.channel_values["ch1"] == 42
    assert loaded.channel_values["ch2"] == "test_value"
    assert loaded.node_version["node1"] == 1


@pytest.mark.asyncio
async def test_graph_store_get_nonexistent(redis_store):
    """Test get when state doesn't exist."""
    store = GraphStore(redis_store)
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    ns = "nonexistent"

    loaded = await store.get(session_id, ns)
    assert loaded is None


@pytest.mark.asyncio
async def test_graph_store_delete(redis_store):
    """Test delete method."""
    store = GraphStore(redis_store)
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    ns = "test_namespace"

    # Save state
    graph_state = create_state(
        ns=ns,
        step=1,
        channel_snapshot={"ch1": 42},
        pending_buffer=[],
        pending_node={},
        node_version={},
    )
    await store.save(session_id, ns, graph_state)

    # Verify exists
    loaded = await store.get(session_id, ns)
    assert loaded is not None

    # Delete
    await store.delete(session_id, ns)

    # Verify deleted
    loaded = await store.get(session_id, ns)
    assert loaded is None


@pytest.mark.asyncio
async def test_graph_store_delete_with_pattern(redis_store):
    """Test delete with namespace pattern."""
    store = GraphStore(redis_store)
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"

    # Save multiple namespaces
    for i in range(3):
        ns = f"ns_{i}"
        graph_state = create_state(
            ns=ns,
            step=1,
            channel_snapshot={"ch": i},
            pending_buffer=[],
            pending_node={},
            node_version={},
        )
        await store.save(session_id, ns, graph_state)

    # Delete all namespaces
    await store.delete(session_id, None)

    # Verify all deleted
    for i in range(3):
        loaded = await store.get(session_id, f"ns_{i}")
        assert loaded is None


@pytest.mark.asyncio
async def test_graph_store_ttl_refresh_on_read(redis_client):
    """Test TTL refresh on read."""
    ttl = {
        "default_ttl": 1,  # 1 minute = 60 seconds
        "refresh_on_read": True,
    }
    redis_store = RedisStore(redis_client)
    store = GraphStore(redis_store, ttl=ttl)
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    ns = "test_namespace"

    # Save state
    graph_state = create_state(
        ns=ns,
        step=1,
        channel_snapshot={"ch1": 42},
        pending_buffer=[],
        pending_node={},
        node_version={},
    )
    await store.save(session_id, ns, graph_state)

    # Get TTL before read
    key_type = build_key_with_namespace(session_id, WORKFLOW_NAMESPACE_GRAPH, ns, store._DATA_TYPE)
    ttl_before = await redis_client.ttl(key_type)

    # Wait a bit
    await asyncio.sleep(0.1)

    # Get (read) - should refresh TTL
    await store.get(session_id, ns)

    # Get TTL after read
    ttl_after = await redis_client.ttl(key_type)

    # TTL should be refreshed (approximately 60 seconds)
    assert ttl_after >= 59  # Allow 1 second tolerance


@pytest.mark.asyncio
async def test_graph_store_save_with_ttl(redis_client):
    """Test save with TTL."""
    ttl = {
        "default_ttl": 1,  # 1 minute = 60 seconds
    }
    redis_store = RedisStore(redis_client)
    store = GraphStore(redis_store, ttl=ttl)
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    ns = "test_namespace"

    # Save state
    graph_state = create_state(
        ns=ns,
        step=1,
        channel_snapshot={"ch1": 42},
        pending_buffer=[],
        pending_node={},
        node_version={},
    )
    await store.save(session_id, ns, graph_state)

    # Check TTL
    key_type = build_key_with_namespace(session_id, WORKFLOW_NAMESPACE_GRAPH, ns, store._DATA_TYPE)
    ttl_value = await redis_client.ttl(key_type)

    # TTL should be approximately 60 seconds
    assert ttl_value >= 59  # Allow 1 second tolerance
    assert ttl_value <= 60


@pytest.mark.asyncio
async def test_integration_redis_checkpoint_saver_basic(redis_store):
    """Test basic Redis checkpoint saver functionality with Message objects."""
    saver = GraphStore(redis_store)
    conversation_id = "conv_redis_123"
    ns = "default"

    for i in range(10):
        ns = f"{ns}_{i}"
        # Create a mock checkpoint
        checkpoint = create_state(
            ns=ns,
            step=1,
            channel_snapshot={"ch1": 42, "ch2": "test_value"},
            pending_buffer=[
                Message(sender="node1", target="node2", payload="pending msg1"),
                Message(sender="node1", target="node2", payload="pending msg2")
            ],
            pending_node={
                "node1": PendingNode(node_name="n1", status="running"),
                "node2": PendingNode(node_name="n2", status="completed")
            }
        )

        # Save checkpoint
        await saver.save(conversation_id, ns, checkpoint)

    # Get checkpoint
    loaded = await saver.get(conversation_id, ns)
    loaded_5 = await saver.get(conversation_id, "default_5")

    assert loaded is not None, "Loaded checkpoint should not be None."
    assert loaded.step == 1
    assert loaded.channel_values["ch1"] == 42
    assert loaded.channel_values["ch2"] == "test_value"
    assert loaded.pending_buffer[0].payload == "pending msg1"
    assert loaded.pending_buffer[1].payload == "pending msg2"
    assert loaded.pending_node["node1"].status == "running"
    assert loaded.pending_node["node1"].node_name == "n1"
    assert loaded.pending_node["node2"].status == "completed"
    assert loaded.pending_node["node2"].node_name == "n2"

    # Delete conversation
    ns = "default"
    await saver.delete(conversation_id, ns)
    deleted = await saver.get(conversation_id, ns)
    assert deleted is None, "After delete, checkpoint should be None."
