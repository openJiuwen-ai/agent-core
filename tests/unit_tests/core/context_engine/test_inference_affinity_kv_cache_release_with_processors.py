# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Integration tests for ContextEngine + InferenceAffinityModel + processors (CE + model layer).

**Scope**: Full flow from ContextEngine.create_context + MessageOffloader to
get_context_window(model=...) and release() call/logs. Uses mocked InferenceAffinityModel
invoke and patched InferenceAffinityModelClient.release. Verifies:
- When processors (e.g. MessageOffloader) modify context, release is triggered and
  session_id / block_released / cache_salt are correct in call and logs.
- When no processors modify context, release is NOT called and no release logs.

Does NOT cover:
ReActAgent entry (warning for non-InferenceAffinity, model injection)
or KVCacheManager logic in isolation; see test_react_agent_kv_cache_release.py and
test_kv_cache_manager.py.
"""

import asyncio
import json
import logging
import re
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloaderConfig,
)
from openjiuwen.core.foundation.llm import UserMessage, ToolMessage, AssistantMessage
from openjiuwen.core.foundation.llm.inference_affinity_model import InferenceAffinityModel
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.session.agent import create_agent_session

pytestmark = pytest.mark.asyncio


def _build_mock_inference_affinity_model() -> InferenceAffinityModel:
    """
    Create a mocked InferenceAffinityModel instance for testing.

    The mock model is configured with test parameters and has its invoke method
    mocked to return a simple response without making actual API calls.

    Returns:
        InferenceAffinityModel: A mocked model instance with stubbed invoke method
    """
    # Configure model request parameters
    model_req_cfg = ModelRequestConfig(
        model="mock-model",
        temperature=0.95,
        top_p=0.1,
        max_tokens=2048,
    )

    # Configure model client connection settings
    client_cfg = ModelClientConfig(
        client_id="mock_test",
        client_provider="InferenceAffinity",
        api_key="mock_key",
        api_base="http://mock-server:8000",
        timeout=60,
        max_retries=3,
        verify_ssl=False,
    )

    # Create the model instance
    model = InferenceAffinityModel(
        model_client_config=client_cfg,
        model_config=model_req_cfg,
    )

    # Mock the invoke method to avoid actual API calls
    async def mock_invoke(*args, **kwargs):
        return MagicMock(content="Mocked response")

    model.invoke = AsyncMock(side_effect=mock_invoke)

    return model


def _extract_metadata_from_record(record) -> dict:
    """
    Extract metadata from a log record.

    This helper function attempts to extract metadata from various possible
    locations in a LogRecord object, supporting different logging configurations.

    Args:
        record: A logging.LogRecord object

    Returns:
        dict: Extracted metadata, or empty dict if not found
    """
    # Check if metadata exists directly in record attributes
    if hasattr(record, 'metadata'):
        return record.metadata

    # Try to extract from common extra fields
    for attr in ['metadata', 'response', 'session_id', 'block_released']:
        if hasattr(record, attr):
            return {attr: getattr(record, attr)}

    return {}


@pytest.mark.asyncio
@patch(
    'openjiuwen.core.foundation.llm.model_clients.inference_affinity_model_client.InferenceAffinityModelClient.release')
async def test_inference_affinity_kv_cache_release_with_message_offloader(mock_release, caplog):
    """
    Test KV cache release when MessageOffloader modifies context messages.

    This test verifies the complete workflow:
    1. Context is created with initial messages including a large tool message
    2. MessageOffloader is configured to offload tool messages above threshold
    3. When new messages are added, MessageOffloader triggers and modifies context
    4. KV cache release is triggered due to message modification
    5. Release operation is properly logged with correct session_id and block count

    Args:
        mock_release: Mocked release method from InferenceAffinityModelClient
        caplog: Pytest fixture for capturing log messages
    """
    # Dictionary to track release call details for verification
    # This avoids parsing log messages and provides direct access to call parameters
    release_call_details = {}

    # Mock implementation of the release method
    async def mock_release_impl(*args, **kwargs):
        """
        Simulates successful KV cache release and logs the operation.

        Captures the session_id, block_released count, and cache_salt for later
        verification in test assertions.
        """
        # Extract and store session_id from call parameters
        session_id = kwargs.get("session_id", "")
        release_call_details['session_id'] = session_id
        release_call_details['block_released'] = 5  # Simulated block count
        release_call_details['cache_salt'] = session_id

        # Log the successful release with structured metadata
        # This mimics the real release method's logging behavior
        llm_logger = logging.getLogger('openjiuwen.core.foundation.llm')
        llm_logger.info(
            "KV cache release successful.",
            extra={
                "event_type": "LLM_CALL_END",
                "metadata": {
                    "client_name": "InferenceAffinity client",
                    "session_id": session_id,
                    "response": {
                        "cache_salt": session_id,
                        "block_released": 5
                    }
                },
                "model_name": kwargs.get("model", "mock-model"),
                "model_provider": "InferenceAffinity",
                "is_stream": False
            }
        )
        return True

    mock_release.side_effect = mock_release_impl
    caplog.set_level(logging.INFO)

    # Configure ContextEngine with KV cache release enabled
    engine_config = ContextEngineConfig(
        enable_kv_cache_release=True,
        default_window_message_num=100,
    )
    engine = ContextEngine(engine_config)

    # Configure MessageOffloader to trigger on 3+ messages
    # This will cause offloading when we add new messages later
    offloader_cfg = MessageOffloaderConfig(
        messages_threshold=3,  # Trigger when message count exceeds 3
        tokens_threshold=100000,
        large_message_threshold=50,  # Messages > 50 chars are "large"
        trim_size=10,
        offload_message_type=["tool"],  # Only offload tool messages
        keep_last_round=False,
    )

    # Set up test environment
    affinity_model = _build_mock_inference_affinity_model()
    agent_session = create_agent_session()
    session_id = agent_session.get_session_id()

    # Create initial messages with a large tool message
    # The tool message exceeds large_message_threshold (100 > 50)
    large_tool_content = "X" * 100
    initial_messages: List = [
        UserMessage(content="u0"),
        ToolMessage(content=large_tool_content, tool_call_id="t1"),
        AssistantMessage(content="a0"),
    ]

    # Create context with MessageOffloader processor
    context = await engine.create_context(
        "affinity_offload_ctx",
        agent_session,
        history_messages=initial_messages,
        processors=[("MessageOffloader", offloader_cfg)],
        token_counter=None,
    )

    # Initial model invocation to establish KV cache state
    ctx_before = await context.get_context_window(model=affinity_model)
    messages_for_model = ctx_before.get_messages()
    await affinity_model.invoke(
        messages=messages_for_model,
        model="mock-model",
        session_id=session_id,
        enable_cache_sharing=True,
    )

    # Clear logs and tracking data before the test action
    caplog.clear()
    release_call_details.clear()

    # Add new messages - this will trigger MessageOffloader
    # Total messages: 3 initial + 2 new = 5, which exceeds threshold of 3
    await context.add_messages([
        UserMessage(content="Follow up question"),
        AssistantMessage(content="Follow up answer"),
    ])

    ctx_after = await context.get_context_window(model=affinity_model)

    # === Verification Phase ===

    # 1. Verify MessageOffloader was triggered
    offload_logs = [
        record for record in caplog.records
        if "MessageOffloader triggered" in record.message
    ]
    assert len(offload_logs) > 0, (
        f"Expected MessageOffloader triggered log not found. All logs:\n{caplog.text}"
    )

    # 2. Verify release reason was logged
    # Release should be triggered because MessageOffloader modified the context
    release_reason_logs = [
        record for record in caplog.records
        if "RELEASE REASON" in record.message and "Message modified" in record.message
    ]
    assert len(release_reason_logs) > 0, (
        f"Expected RELEASE REASON log not found. All logs:\n{caplog.text}"
    )

    # 3. Verify release method was actually called
    assert mock_release.called, "Expected release method to be called"

    # 4. Verify correct session_id was passed to release method
    call_kwargs = mock_release.call_args.kwargs if mock_release.call_args else {}
    assert call_kwargs.get("session_id") == session_id, (
        f"Expected session_id '{session_id}' in release call, but got: {call_kwargs.get('session_id')}"
    )

    # 5. Verify KV cache release success was logged
    release_success_logs = [
        record for record in caplog.records
        if "KV cache release successful" in record.message
    ]
    assert len(release_success_logs) > 0, (
        f"Expected 'KV cache release successful' log not found. All logs:\n{caplog.text}"
    )

    # 6. Verify block_released count from tracked call details
    # We use release_call_details instead of parsing logs for reliability
    assert 'block_released' in release_call_details, (
        "block_released not found in release call details"
    )

    block_released = release_call_details.get('block_released', 0)
    assert block_released > 0, (
        f"Expected block_released > 0, but got {block_released}"
    )

    # 7. Verify cache_salt matches session_id
    # cache_salt should be identical to session_id for proper cache identification
    cache_salt = release_call_details.get('cache_salt', '')
    assert cache_salt == session_id, (
        f"Expected cache_salt '{session_id}', but got '{cache_salt}'"
    )

    # 8. Verify tool message was actually offloaded
    # After offloading, tool message should be replaced or truncated
    tool_messages_after = [
        m for m in ctx_after.get_messages()
        if "tool" in m.role.lower()
    ]
    if tool_messages_after:
        tool_msg = tool_messages_after[0]
        assert "OFFLOAD" in str(tool_msg.content) or len(tool_msg.content) < len(large_tool_content), (
            f"Expected tool message to be offloaded, but got: {tool_msg.content}"
        )


@pytest.mark.asyncio
@patch(
    'openjiuwen.core.foundation.llm.model_clients.inference_affinity_model_client.InferenceAffinityModelClient.release')
async def test_inference_affinity_kv_cache_no_release_without_modification(mock_release, caplog):
    """
    Test that KV cache is NOT released when context messages remain unchanged.

    This is a negative test case verifying that:
    1. No processors are configured that would modify messages
    2. Adding new messages doesn't trigger any modification of existing messages
    3. KV cache release is NOT triggered
    4. No release-related logs are generated

    This ensures the KV cache release mechanism is conservative and only
    releases cache when necessary, avoiding unnecessary performance overhead.

    Args:
        mock_release: Mocked release method from InferenceAffinityModelClient
        caplog: Pytest fixture for capturing log messages
    """
    caplog.set_level(logging.INFO)

    # Configure ContextEngine with KV cache release enabled
    engine_config = ContextEngineConfig(
        enable_kv_cache_release=True,
        default_window_message_num=100,
    )
    engine = ContextEngine(engine_config)

    # Set up test environment
    affinity_model = _build_mock_inference_affinity_model()
    agent_session = create_agent_session()
    session_id = agent_session.get_session_id()

    # Simple initial messages without any large content
    initial_messages: List = [
        UserMessage(content="Hello"),
        AssistantMessage(content="Hi there"),
    ]

    # Create context WITHOUT any processors
    # This means messages will not be modified
    context = await engine.create_context(
        "no_release_ctx",
        agent_session,
        history_messages=initial_messages,
        processors=[],  # No processors = no modifications
        token_counter=None,
    )

    # Initial model invocation
    ctx_before = await context.get_context_window(model=affinity_model)
    await affinity_model.invoke(
        messages=ctx_before.get_messages(),
        model="mock-model",
        session_id=session_id,
        enable_cache_sharing=True,
    )

    # Clear logs and reset mock before test action
    caplog.clear()
    mock_release.reset_mock()

    # Add new messages - no processors will modify existing messages
    await context.add_messages([
        UserMessage(content="How are you?"),
    ])

    ctx_after = await context.get_context_window(model=affinity_model)

    # === Verification Phase ===

    # 1. Verify release method was NOT called
    # Since no messages were modified, there's no reason to release cache
    assert not mock_release.called, (
        f"Expected release method NOT to be called, but it was called {mock_release.call_count} times"
    )

    # 2. Verify NO release-related logs were generated
    release_logs = [
        record for record in caplog.records
        if "KV cache release" in record.message or "RELEASE REASON" in record.message
    ]
    assert len(release_logs) == 0, (
        f"Expected NO release logs, but found: {[r.message for r in release_logs]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])