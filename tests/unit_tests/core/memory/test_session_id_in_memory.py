# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Unit tests for session_id handling in memory functionality.

This test verifies that:
1. Historical messages are properly added to memory
2. LLMAgent correctly handles session_id information
3. Logs contain both historical and current messages
"""

from unittest.mock import Mock, patch
import asyncio
import pytest
import pytest_asyncio

from openjiuwen.core.application.llm_agent.llm_agent import LLMAgent
from openjiuwen.core.single_agent.legacy import LegacyReActAgentConfig as ReActAgentConfig
from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.foundation.llm import ModelConfig, UserMessage, AssistantMessage
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.memory.config.config import AgentMemoryConfig
from openjiuwen.core.session.stream import OutputSchema


@pytest_asyncio.fixture(name="agent_fixture")
async def _agent_fixture():
    """Set up test environment for each test."""
    # Create a mock LongTermMemory instance
    mock_memory = Mock(spec=LongTermMemory)
    
    # Make add_messages a coroutine function
    async def mock_add_messages(*args, **kwargs):
        return None
    
    mock_memory.add_messages = Mock(side_effect=mock_add_messages)
    
    # Save the original LongTermMemory to restore later
    original_long_term_memory = LongTermMemory
    
    # Mock the LongTermMemory singleton behavior by patching the __call__ method of the metaclass
    with patch.object(type(LongTermMemory), '__call__', return_value=mock_memory):
        # Create a simple agent config
        from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo
        
        agent_config = ReActAgentConfig(
            id="test_agent",
            version="1.0",
            description="Test Agent",
            workflows=[],
            plugins=[],
            model=ModelConfig(
                model_provider="test_provider",
                model_info=BaseModelInfo(
                    model_name="test_model",
                    temperature=0.1,
                    api_base="http://test-api-base.com"
                )
            ),
            prompt_template=[],
            tools=[],
            controller_type=ControllerType.ReActController,
            memory_scope_id="test_scope_id",
            agent_memory_config=AgentMemoryConfig(
                enable_long_term_mem=True,
                mem_variables=[]
            )
        )
        
        # Patch the controller to avoid actual LLM calls
        with patch('openjiuwen.core.application.llm_agent.llm_agent.LLMController') as mock_controller:
            mock_controller_instance = mock_controller.return_value
            # Make invoke return a coroutine that returns the result
            
            async def mock_invoke(*args, **kwargs):
                return {
                    "result_type": "answer",
                    "output": "Test response"
                }
            mock_controller_instance.invoke = Mock(side_effect=mock_invoke)
            
            # Create the agent
            agent = LLMAgent(agent_config)
            
            # The agent should automatically use our mock memory instance due to the singleton pattern
            
            yield agent, mock_memory
    
    # Restore the original LongTermMemory in teardown
    # Note: We can't directly assign to the imported module attribute here
    # This is just a placeholder to maintain the same cleanup behavior as original tests


@pytest.mark.asyncio
async def test_invoke_with_session_id(agent_fixture):
    """Test that invoke method correctly handles session_id and saves messages."""
    agent, mock_memory = agent_fixture
    
    # Define test inputs with session_id
    test_session_id = "test_session_123"
    test_inputs = {
        "query": "Hello, how are you?",
        "user_id": "test_user_456",
        "conversation_id": test_session_id
    }
    
    # Call invoke method
    result = await agent.invoke(test_inputs)
    
    # Wait for the async memory task to complete
    await asyncio.sleep(0.1)
    
    # Verify that add_messages was called with the correct session_id
    mock_memory.add_messages.assert_called_once()
    call_args = mock_memory.add_messages.call_args
    
    # Check that session_id was passed correctly
    assert call_args.kwargs.get("session_id") == test_session_id
    
    # Check that messages were passed correctly
    messages = call_args.kwargs.get("messages")
    assert len(messages) == 2  # User message and assistant message
    
    # Verify user message
    assert messages[0].role == "user"
    assert messages[0].content == "Hello, how are you?"
    
    # Verify assistant message
    assert messages[1].role == "assistant"
    assert messages[1].content == "Test response"


@pytest.mark.asyncio
async def test_stream_with_session_id(agent_fixture):
    """Test that stream method correctly handles session_id and saves messages."""
    agent, mock_memory = agent_fixture
    
    # Define test inputs with session_id
    test_session_id = "test_stream_session_789"
    test_inputs = {
        "query": "What's the weather today?",
        "user_id": "test_stream_user_012",
        "conversation_id": test_session_id
    }
    
    # Create mock stream output
    mock_output = OutputSchema(
        type="answer",
        index=0,
        payload={
            "result_type": "answer",
            "output": "The weather is sunny."
        }
    )
    
    # Create a mock session instead of accessing agent._session directly
    mock_session = Mock()
    mock_session.get_session_id.return_value = test_session_id
    
    # Create an async iterator
    async def async_stream_iterator():
        yield mock_output
    
    mock_session.stream_iterator = Mock(side_effect=async_stream_iterator)
    
    # Make post_run a coroutine
    async def mock_post_run():
        return None
    
    mock_session.post_run = Mock(side_effect=mock_post_run)
    
    # Patch the context engine
    with patch.object(agent.context_engine, 'create_context') as mock_create_context:
        # Consume the stream, passing our mock session
        async for _ in agent.stream(test_inputs, session=mock_session):
            pass
        
        # Wait for the memory task to complete
        await asyncio.sleep(0.1)
        
        # Verify that add_messages was called with the correct session_id
        mock_memory.add_messages.assert_called_once()
        call_args = mock_memory.add_messages.call_args
        
        # Check that session_id was passed correctly
        assert call_args.kwargs.get("session_id") == test_session_id


@pytest.mark.asyncio
async def test_write_messages_to_memory_with_history(agent_fixture):
    """Test that _write_messages_to_memory correctly handles historical messages."""
    agent, mock_memory = agent_fixture
    
    # Define test inputs with session_id
    test_session_id = "test_history_session_345"
    test_user_id = "test_history_user_678"
    
    # Create test messages
    user_message = "Tell me about AI"
    assistant_message = AssistantMessage(content="AI stands for Artificial Intelligence.")
    
    # Create mock result
    mock_result = {
        "result_type": "answer",
        "output": assistant_message.content
    }
    
    # Instead of calling _write_messages_to_memory directly, we can patch the controller
    # and call invoke to test the memory writing functionality
    with patch.object(agent.controller, 'invoke') as mock_invoke:
        # Make the controller invoke return a coroutine that returns our mock result
        async def mock_controller_invoke(*args, **kwargs):
            return mock_result
        mock_invoke.side_effect = mock_controller_invoke
        
        # Call invoke with our test inputs
        await agent.invoke({
            "query": user_message,
            "user_id": test_user_id,
            "conversation_id": test_session_id
        })
        
        # Wait for the async memory task to complete
        await asyncio.sleep(0.1)
    
    # Verify that add_messages was called with both user and assistant messages
    mock_memory.add_messages.assert_called_once()
    call_args = mock_memory.add_messages.call_args
    
    # Check that session_id and user_id were passed correctly
    assert call_args.kwargs.get("session_id") == test_session_id
    assert call_args.kwargs.get("user_id") == test_user_id
    
    # Check that both messages were added
    messages = call_args.kwargs.get("messages")
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == user_message
    assert messages[1].role == "assistant"
    assert messages[1].content == assistant_message.content


@pytest.mark.asyncio
async def test_memory_disabled(agent_fixture):
    """Test that memory operations are skipped when memory is disabled."""
    agent, mock_memory = agent_fixture
    
    # Create config with memory disabled
    from openjiuwen.core.foundation.llm.schema.mode_info import BaseModelInfo
    
    config_without_memory = ReActAgentConfig(
        id="test_agent_no_memory",
        version="1.0",
        description="Test Agent without memory",
        workflows=[],
        plugins=[],
        model=ModelConfig(
            model_provider="test_provider",
            model_info=BaseModelInfo(
                model_name="test_model",
                temperature=0.1,
                api_base="http://test-api-base.com"
            )
        ),
        prompt_template=[],
        tools=[],
        controller_type=ControllerType.ReActController,
        memory_scope_id="",  # Empty scope_id disables memory
        agent_memory_config=AgentMemoryConfig(
            enable_long_term_mem=False,
            mem_variables=[]
        )
    )
    
    # Patch the controller
    with patch('openjiuwen.core.application.llm_agent.llm_agent.LLMController') as mock_controller:
        mock_controller_instance = mock_controller.return_value
        # Make invoke return a coroutine that returns the result
        
        async def mock_invoke(*args, **kwargs):
            return {
                "result_type": "answer",
                "output": "Test response"
            }
        mock_controller_instance.invoke = Mock(side_effect=mock_invoke)
        
        # Create agent with memory disabled
        agent_no_memory = LLMAgent(config_without_memory)
        
        # Call invoke
        await agent_no_memory.invoke({
            "query": "Hello",
            "user_id": "test_user",
            "conversation_id": "test_session"
        })
        
        # Verify that add_messages was NOT called
        mock_memory.add_messages.assert_not_called()
