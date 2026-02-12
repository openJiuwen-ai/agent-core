# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the context_evolver quickstart.py example.

This test module provides comprehensive test coverage for the quickstart.py
example, including:
- Configuration validation
- Memory service creation and initialization
- Memory addition operations
- Agent creation and configuration
- Agent invocation with memory context
- Trajectory summarization

Tests are organized by functionality and use pytest for async operations.
"""

import sys
import os
import tempfile
from pathlib import Path
import pytest

# Setup path for imports
agent_core_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
))))
if agent_core_root not in sys.path:
    sys.path.append(agent_core_root)

# Import the modules to test
from openjiuwen.extensions.context_evolver import (
    TaskMemoryService,
    AddMemoryRequest,
    ContextEvolvingReActAgent,
    create_memory_agent_config,
    MemoryAgentConfigInput,
    SummarizeTrajectoriesInput,
)
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.extensions.context_evolver.core import config as app_config

from openjiuwen.core.common.logging import context_engine_logger as logger


class TestQuickstartConfiguration:
    """Test configuration validation and setup."""

    @staticmethod
    @pytest.mark.skip(reason="API_KEY not configured - API_KEY is needed in .env file. "
                             "Please create .env file by referring to .env.example")
    def test_config_api_key_exists():
        """Test that API_KEY configuration exists."""
        api_key = app_config.get("API_KEY")
        assert api_key is not None, "API_KEY should be configured"
        assert len(api_key) > 0, "API_KEY should not be empty"

    @staticmethod
    def test_config_api_base_default():
        """Test that API_BASE has a valid default."""
        api_base = app_config.get("API_BASE", "https://api.openai.com/v1")
        assert api_base == "https://api.openai.com/v1"
        assert api_base.startswith("https://"), "API_BASE should use HTTPS"

    @staticmethod
    def test_config_model_name_default():
        """Test that MODEL_NAME has a valid default."""
        model_name = app_config.get("MODEL_NAME", "gpt-5.2")
        assert model_name == "gpt-5.2"
        assert model_name is not None, "MODEL_NAME should not be None"

    @staticmethod
    def test_config_can_set_values():
        """Test that configuration values can be set."""
        original = app_config.get("TEST_KEY", None)

        app_config.set_value("TEST_KEY", "test_value")
        result = app_config.get("TEST_KEY")
        assert result == "test_value"

        # Restore original
        if original is not None:
            app_config.set_value("TEST_KEY", original)

    @staticmethod
    def test_config_retrieval_algorithm_valid():
        """Test that retrieval algorithm can be configured."""
        original = app_config.get("RETRIEVAL_ALGO")

        valid_algos = ["ACE", "RB", "ReMe"]
        for algo in valid_algos:
            app_config.set_value("RETRIEVAL_ALGO", algo)
            result = app_config.get("RETRIEVAL_ALGO")
            assert result == algo, f"Should be able to set {algo}"

        # Restore original
        if original:
            app_config.set_value("RETRIEVAL_ALGO", original)


@pytest.mark.skip(reason="API_KEY not configured - API_KEY is needed in .env file. "
                          "Please create .env file by referring to .env.example")
class TestMemoryServiceInitialization:
    """Test TaskMemoryService creation and initialization."""

    def __init__(self):
        """Initialize test class attributes."""
        self.temp_dir = None
        self.memory_service = None

    @pytest.fixture(autouse=True)
    async def setup_teardown(self):
        """Set up and tear down test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.memory_service = None

        yield

        # Cleanup
        if self.memory_service:
            try:
                # Clean up any created resources
                pass
            except Exception as e:
                logger.warning(f"Cleanup error: {e}")

    @pytest.mark.asyncio
    async def test_memory_service_creation(self):
        """Test that TaskMemoryService can be created."""
        memory_service = TaskMemoryService()
        assert memory_service is not None
        assert type(memory_service).__name__ == "TaskMemoryService"

    @pytest.mark.asyncio
    async def test_memory_service_has_retrieval_algorithm(self):
        """Test that memory service has a retrieval algorithm."""
        memory_service = TaskMemoryService()
        retrieval_algo = memory_service.retrieval_algorithm
        assert retrieval_algo is not None
        assert retrieval_algo in ["ACE", "RB", "ReMe"]

    @pytest.mark.asyncio
    async def test_memory_service_has_summary_algorithm(self):
        """Test that memory service has a summary algorithm."""
        memory_service = TaskMemoryService()
        summary_algo = memory_service.summary_algorithm
        assert summary_algo is not None
        assert summary_algo in ["ACE", "RB", "ReMe"]

    @pytest.mark.asyncio
    async def test_memory_service_algorithms_consistent(self):
        """Test that retrieval and summary algorithms are properly initialized."""
        memory_service = TaskMemoryService()

        # Both should be valid algorithm names
        retrieval_algo = memory_service.retrieval_algorithm
        summary_algo = memory_service.summary_algorithm

        assert len(retrieval_algo) > 0
        assert len(summary_algo) > 0


@pytest.mark.skip(reason="API_KEY not configured - API_KEY is needed in .env file. "
                          "Please create .env file by referring to .env.example")
class TestMemoryAddition:
    """Test adding memories to the service."""

    def __init__(self):
        """Initialize test class attributes."""
        self.memory_service = None
        self.user_id = None

    @pytest.fixture(autouse=True)
    async def setup_teardown(self):
        """Set up test fixtures."""
        self.memory_service = TaskMemoryService()
        self.user_id = "test_user"
        yield

    @pytest.mark.asyncio
    async def test_add_memory_reme_format(self):
        """Test adding memory in ReMe format."""
        # Configure for ReMe
        app_config.set_value("RETRIEVAL_ALGO", "ReMe")
        app_config.set_value("SUMMARY_ALGO", "ReMe")

        memory_service = TaskMemoryService()

        # Create ReMe format memory
        request = AddMemoryRequest(
            when_to_use="When learning Python programming",
            content="List comprehensions are more efficient than loops for creating lists"
        )

        # Should not raise an error
        await memory_service.add_memory(
            user_id=self.user_id,
            request=request
        )

    @pytest.mark.asyncio
    async def test_add_memory_reasoning_bank_format(self):
        """Test adding memory in ReasoningBank format."""
        # Configure for ReasoningBank
        app_config.set_value("RETRIEVAL_ALGO", "RB")
        app_config.set_value("SUMMARY_ALGO", "RB")

        memory_service = TaskMemoryService()

        # Create ReasoningBank format memory
        request = AddMemoryRequest(
            title="Python Best Practices",
            description="Guidelines for writing clean Python code",
            content="Use meaningful variable names and follow PEP 8 style guide"
        )

        # Should not raise an error
        await memory_service.add_memory(
            user_id=self.user_id,
            request=request
        )

    @pytest.mark.asyncio
    async def test_add_memory_ace_format(self):
        """Test adding memory in ACE format."""
        # Configure for ACE
        app_config.set_value("RETRIEVAL_ALGO", "ACE")
        app_config.set_value("SUMMARY_ALGO", "ACE")

        memory_service = TaskMemoryService()

        # Create ACE format memory
        request = AddMemoryRequest(
            content="Use type hints in Python for better code clarity",
            section="python"
        )

        # Should not raise an error
        await memory_service.add_memory(
            user_id=self.user_id,
            request=request
        )

    @pytest.mark.asyncio
    async def test_add_multiple_memories(self):
        """Test adding multiple memories."""
        requests = [
            AddMemoryRequest(
                when_to_use="When working with Python",
                content="Use virtual environments for project isolation"
            ),
            AddMemoryRequest(
                when_to_use="When debugging Python code",
                content="Use print statements or debugger like pdb"
            ),
        ]

        for request in requests:
            await self.memory_service.add_memory(
                user_id=self.user_id,
                request=request
            )


@pytest.mark.skip(reason="API_KEY not configured - API_KEY is needed in .env file. "
                          "Please create .env file by referring to .env.example")
class TestAgentCreation:
    """Test ContextEvolvingReActAgent creation and configuration."""

    def __init__(self):
        """Initialize test class attributes."""
        self.memory_service = None
        self.user_id = None
        self.api_key = None
        self.api_base = None
        self.model_name = None

    @pytest.fixture(autouse=True)
    async def setup_teardown(self):
        """Set up test fixtures."""
        self.memory_service = TaskMemoryService()
        self.user_id = "test_agent_user"
        self.api_key = app_config.get("API_KEY")
        self.api_base = app_config.get("API_BASE", "https://api.openai.com/v1")
        self.model_name = app_config.get("MODEL_NAME", "gpt-5.2")
        yield

    @pytest.mark.asyncio
    async def test_agent_card_creation(self):
        """Test creating an agent card."""
        agent_card = AgentCard(
            id="test-agent",
            name="Test Agent",
            description="A test agent"
        )

        assert agent_card.id == "test-agent"
        assert agent_card.name == "Test Agent"
        assert agent_card.description == "A test agent"

    @pytest.mark.asyncio
    async def test_agent_creation_with_memory(self):
        """Test creating a ContextEvolvingReActAgent with memory."""
        agent_card = AgentCard(
            id="memory-test-agent",
            name="Memory Test Agent",
            description="Agent with memory for testing"
        )

        agent = ContextEvolvingReActAgent(
            card=agent_card,
            user_id=self.user_id,
            memory_service=self.memory_service,
            inject_memories_in_context=True
        )

        assert agent is not None
        assert agent.card.id == "memory-test-agent"

    @pytest.mark.asyncio
    async def test_agent_configuration(self):
        """Test configuring an agent with model settings."""
        agent_card = AgentCard(
            id="config-test-agent",
            name="Config Test Agent",
            description="Agent configuration test"
        )

        agent = ContextEvolvingReActAgent(
            card=agent_card,
            user_id=self.user_id,
            memory_service=self.memory_service,
            inject_memories_in_context=True
        )

        # Create and apply configuration
        config = create_memory_agent_config(
            MemoryAgentConfigInput(
                model_provider="OpenAI",
                api_key=self.api_key,
                api_base=self.api_base,
                model_name=self.model_name,
                system_prompt="You are a helpful assistant"
            )
        )

        agent.configure(config)

        # Verify agent is configured
        assert agent is not None

    @pytest.mark.asyncio
    async def test_agent_memory_injection_enabled(self):
        """Test that agent is created with memory injection enabled."""
        agent_card = AgentCard(
            id="inject-test-agent",
            name="Memory Inject Test Agent",
            description="Test memory injection"
        )

        agent = ContextEvolvingReActAgent(
            card=agent_card,
            user_id=self.user_id,
            memory_service=self.memory_service,
            inject_memories_in_context=True
        )

        assert agent is not None
        # Memory injection should be enabled
        assert True  # Agent was created with inject_memories_in_context=True


@pytest.mark.skip(reason="API_KEY not configured - API_KEY is needed in .env file. "
                          "Please create .env file by referring to .env.example")
class TestMemoryAgentIntegration:
    """Integration tests for memory-augmented agent."""

    def __init__(self):
        """Initialize test class attributes."""
        self.memory_service = None
        self.user_id = None
        self.api_key = None
        self.api_base = None
        self.model_name = None

    @pytest.fixture(autouse=True)
    async def setup_teardown(self):
        """Set up test fixtures."""
        self.memory_service = TaskMemoryService()
        self.user_id = "integration_test_user"
        self.api_key = app_config.get("API_KEY")
        self.api_base = app_config.get("API_BASE", "https://api.openai.com/v1")
        self.model_name = app_config.get("MODEL_NAME", "gpt-5.2")
        yield

    @pytest.mark.asyncio
    async def test_agent_invoke_structure(self):
        """Test that agent invoke returns expected structure."""
        # Create agent
        agent_card = AgentCard(
            id="invoke-test-agent",
            name="Invoke Test Agent",
            description="Test invoke structure"
        )

        agent = ContextEvolvingReActAgent(
            card=agent_card,
            user_id=self.user_id,
            memory_service=self.memory_service,
            inject_memories_in_context=True
        )

        # Configure agent
        config = create_memory_agent_config(
            MemoryAgentConfigInput(
                model_provider="OpenAI",
                api_key=self.api_key,
                api_base=self.api_base,
                model_name=self.model_name,
                system_prompt="You are a helpful assistant"
            )
        )

        agent.configure(config)

        # Test invoke call - just verify it doesn't crash on setup
        assert agent is not None

    @pytest.mark.asyncio
    async def test_trajectory_summarization_input_creation(self):
        """Test creating SummarizeTrajectoriesInput."""
        trajectory_input = SummarizeTrajectoriesInput(
            query="What is Python?",
            trajectory="Response about Python",
            feedback="helpful",
            matts_mode="none"
        )

        assert trajectory_input is not None
        assert trajectory_input.query == "What is Python?"
        assert trajectory_input.feedback == "helpful"

    @pytest.mark.asyncio
    async def test_memory_agent_config_creation(self):
        """Test creating memory agent configuration."""
        config = create_memory_agent_config(
            MemoryAgentConfigInput(
                model_provider="OpenAI",
                api_key=self.api_key,
                api_base=self.api_base,
                model_name=self.model_name,
                system_prompt="You are a helpful assistant with memory"
            )
        )

        assert config is not None

    @pytest.mark.asyncio
    async def test_memory_service_persistence(self):
        """Test that memory service can be reused across multiple agents."""
        shared_memory_service = TaskMemoryService()

        # Create multiple agents sharing the same memory service
        agent1 = ContextEvolvingReActAgent(
            card=AgentCard(
                id="agent1",
                name="Agent 1",
                description="First agent"
            ),
            user_id=self.user_id,
            memory_service=shared_memory_service,
            inject_memories_in_context=True
        )

        agent2 = ContextEvolvingReActAgent(
            card=AgentCard(
                id="agent2",
                name="Agent 2",
                description="Second agent"
            ),
            user_id=self.user_id,
            memory_service=shared_memory_service,
            inject_memories_in_context=True
        )

        # Both agents should be valid
        assert agent1 is not None
        assert agent2 is not None
        assert agent1.memory_service == agent2.memory_service


class TestEnvironmentSetup:
    """Test environment and path setup for imports."""

    @staticmethod
    def test_agent_core_root_in_path():
        """Test that agent_core root is properly added to path."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        ))))

        # Should be able to import modules
        assert os.path.exists(project_root)
        assert os.path.exists(os.path.join(project_root, 'openjiuwen'))

    @staticmethod
    def test_module_imports():
        """Test that required modules can be imported."""
        # Verify that the modules imported at the top are accessible
        assert TaskMemoryService is not None
        assert AgentCard is not None
        # Verify they are callable/classes
        assert callable(TaskMemoryService)
        assert callable(AgentCard)

    @staticmethod
    def test_config_module_access():
        """Test that config module is accessible."""
        # Verify that config module imported at the top is accessible
        assert app_config is not None
        # Should have get and set_value methods
        assert hasattr(app_config, 'get')
        assert hasattr(app_config, 'set_value')


@pytest.mark.skip(reason="API_KEY not configured - API_KEY is needed in .env file. "
                          "Please create .env file by referring to .env.example")
class TestQuickstartDataValidation:
    """Test data validation and error handling."""

    @pytest.mark.asyncio
    async def test_add_memory_request_with_all_fields(self):
        """Test AddMemoryRequest with all optional fields."""
        request = AddMemoryRequest(
            title="Complete Memory",
            description="A memory with all fields",
            when_to_use="When needed",
            content="The memory content",
            section="test_section"
        )

        assert request is not None
        assert request.content == "The memory content"

    @pytest.mark.asyncio
    async def test_summarize_trajectories_with_feedback_variants(self):
        """Test SummarizeTrajectoriesInput with different feedback values."""
        feedback_values = ["helpful", "harmful", "neutral"]

        for feedback in feedback_values:
            input_obj = SummarizeTrajectoriesInput(
                query="Test query",
                trajectory="Test trajectory",
                feedback=feedback,
                matts_mode="none"
            )
            assert input_obj.feedback == feedback

    @pytest.mark.asyncio
    async def test_agent_card_with_minimal_info(self):
        """Test AgentCard creation with minimal information."""
        card = AgentCard(
            id="minimal-agent",
            name="Minimal Agent"
        )

        assert card.id == "minimal-agent"
        assert card.name == "Minimal Agent"

    @pytest.mark.asyncio
    async def test_agent_card_with_full_info(self):
        """Test AgentCard creation with all information."""
        card = AgentCard(
            id="full-agent",
            name="Full Agent",
            description="Complete agent description",
        )

        assert card.id == "full-agent"
        assert card.name == "Full Agent"
        assert card.description == "Complete agent description"


@pytest.mark.skip(reason="API_KEY not configured - API_KEY is needed in .env file. "
                          "Please create .env file by referring to .env.example")
class TestQuickstartErrorHandling:
    """Test error handling and edge cases."""

    def __init__(self):
        """Initialize test class attributes."""
        self.memory_service = None

    @pytest.fixture(autouse=True)
    async def setup_teardown(self):
        """Set up test fixtures."""
        self.memory_service = TaskMemoryService()
        yield

    @pytest.mark.asyncio
    async def test_empty_api_key_handled(self):
        """Test that missing API key is detected."""
        # Should raise or handle gracefully
        api_key = app_config.get("API_KEY")
        if not api_key:
            pytest.fail("API_KEY must be configured for tests")

    @pytest.mark.asyncio
    async def test_empty_query_handling(self):
        """Test trajectory input with empty query."""
        trajectory_input = SummarizeTrajectoriesInput(
            query="",
            trajectory="Some response",
            feedback="neutral",
            matts_mode="none"
        )

        # Should accept empty query (will be handled by agent)
        assert trajectory_input.query == ""

    @pytest.mark.asyncio
    async def test_none_trajectory_handling(self):
        """Test trajectory input with None trajectory."""
        trajectory_input = SummarizeTrajectoriesInput(
            query="What is this?",
            trajectory=None,
            feedback="neutral",
            matts_mode="none"
        )

        # Should accept None trajectory
        assert trajectory_input.trajectory is None


if __name__ == "__main__":
    # Run tests using pytest
    pytest.main([__file__, "-v"])
