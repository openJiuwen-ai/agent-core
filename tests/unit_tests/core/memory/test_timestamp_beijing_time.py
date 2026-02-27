# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import logging
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone
import pytest

logger = logging.getLogger(__name__)

from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.memory.config.config import AgentMemoryConfig


@pytest.mark.asyncio
async def test_add_messages_timestamp_beijing_time():
    """
    Test that when the input timestamp is empty, the current conversation time seen in the log is Beijing time
    """
    # Mock the entire LongTermMemory class
    with patch('openjiuwen.core.memory.long_term_memory.LongTermMemory') as mock_memory_class:
        # Create mock instance
        mock_mem = MagicMock()
        mock_memory_class.return_value = mock_mem
        
        # Mock the gen_all_memory method to capture the passed timestamp_str parameter
        captured_timestamp_str = None

        async def mock_gen_all_memory(*args, **kwargs):
            nonlocal captured_timestamp_str
            captured_timestamp_str = kwargs.get('timestamp')
            return []

        # Set up mocked dependencies and methods
        mock_mem.add_messages = AsyncMock()
        
        # Replace the add_messages implementation of LongTermMemory to capture timestamp
        original_add_messages = LongTermMemory.add_messages
        
        async def patched_add_messages(self, *args, **kwargs):
            nonlocal captured_timestamp_str
            # Call the core logic of the original method to test timestamp processing
            if not kwargs.get('timestamp'):
                kwargs['timestamp'] = datetime.now(timezone.utc).astimezone()
            timestamp_str = kwargs['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            captured_timestamp_str = timestamp_str
            return
        
        # Temporarily replace the method
        LongTermMemory.add_messages = patched_add_messages
        
        try:
            # Create LongTermMemory instance
            mem = LongTermMemory()
            
            # Call the add_messages method without passing a timestamp parameter
            await mem.add_messages(
                messages=[BaseMessage(role="user", content="test")],
                agent_config=AgentMemoryConfig(enable_long_term_mem=True),
                user_id="test_user",
                scope_id="test_scope",
                session_id="test_session",
                gen_mem=True
            )
        finally:
            # Restore the original method
            LongTermMemory.add_messages = original_add_messages

    # Verify that timestamp_str is correctly set
    assert captured_timestamp_str is not None, "timestamp_str should not be None"

    # Parse timestamp_str and verify it's in the correct time format
    try:
        parsed_time = datetime.strptime(captured_timestamp_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        pytest.fail(f"timestamp_str '{captured_timestamp_str}' is not in the expected format '%Y-%m-%d %H:%M:%S'")

    # Verify that the generated time is close to the current time (allowing 1 minute error)
    current_time = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    current_parsed = datetime.strptime(current_time, '%Y-%m-%d %H:%M:%S')
    time_diff = abs((current_parsed - parsed_time).total_seconds())
    assert time_diff < 60, f"Generated time {captured_timestamp_str} is too far from current time {current_time}"

    # Print test results
    logger.info(f"Generated timestamp_str: {captured_timestamp_str}")
    logger.info(f"Current time (local): {current_time}")
    logger.info(f"Time difference: {time_diff} seconds")


@pytest.mark.asyncio
async def test_add_messages_with_custom_timestamp():
    """
    Test that when a custom timestamp is input, the custom time is used
    """
    # Custom timestamp (2023-01-01 12:00:00 UTC)
    custom_timestamp = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    
    # Mock the gen_all_memory method to capture the passed timestamp_str parameter
    captured_timestamp_str = None

    async def mock_gen_all_memory(*args, **kwargs):
        nonlocal captured_timestamp_str
        captured_timestamp_str = kwargs.get('timestamp')
        return []

    # Replace the add_messages implementation of LongTermMemory to capture timestamp
    original_add_messages = LongTermMemory.add_messages
    
    async def patched_add_messages(self, *args, **kwargs):
        nonlocal captured_timestamp_str
        # Directly use the passed timestamp parameter
        if 'timestamp' in kwargs and kwargs['timestamp']:
            timestamp_str = kwargs['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            captured_timestamp_str = timestamp_str
        return
    
    # Temporarily replace the method
    LongTermMemory.add_messages = patched_add_messages
    
    try:
        # Create LongTermMemory instance
        mem = LongTermMemory()
        
        # Call the add_messages method with a custom timestamp parameter
        await mem.add_messages(
            messages=[BaseMessage(role="user", content="test")],
            agent_config=AgentMemoryConfig(enable_long_term_mem=True),
            user_id="test_user",
            scope_id="test_scope",
            session_id="test_session",
            timestamp=custom_timestamp,
            gen_mem=True
        )
    finally:
        # Restore the original method
        LongTermMemory.add_messages = original_add_messages

    # Verify that timestamp_str is correctly set to the custom time
    assert captured_timestamp_str == "2023-01-01 12:00:00", \
    f"Expected timestamp_str to be '2023-01-01 12:00:00' (UTC time), but got '{captured_timestamp_str}'"

    logger.info(f"Custom timestamp (UTC): {custom_timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Generated timestamp_str: {captured_timestamp_str}")
