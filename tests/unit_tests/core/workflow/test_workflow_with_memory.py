# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for MemoryWriteComponent and MemoryRetrievalComponent.
"""
from datetime import datetime, timezone
import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, BaseMessage
from openjiuwen.core.memory.long_term_memory import MemInfo, MemResult, MemoryType
from openjiuwen.core.session import WorkflowSession, NodeSession
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.resource.memory_write_comp import (
    MemoryWriteCompConfig,
    MemoryWriteExecutable,
    MemoryWriteComponent,
)
from openjiuwen.core.workflow.components.resource.memory_retrieval_comp import (
    MemoryRetrievalCompConfig,
    MemoryRetrievalExecutable,
    MemoryRetrievalComponent,
)
from openjiuwen.core.memory.long_term_memory import LongTermMemory


class MockMemory(LongTermMemory):
    """Mock class for LongTermMemory."""

    def __init__(self):
        super().__init__()
        self.add_messages_called = False
        self.add_messages_args = None
        self.add_messages_kwargs = None
        self.add_messages_error = None
        self.search_user_mem_result = []
        self.search_user_mem_called = False
        self.search_user_mem_kwargs = None
        self.search_user_mem_error = None
        self.search_user_history_summary_result = []
        self.search_user_history_summary_called = False
        self.search_user_history_summary_kwargs = None
        self.search_user_history_summary_error = None

    async def add_messages(self, *args, **kwargs):
        self.add_messages_called = True
        self.add_messages_args = args
        self.add_messages_kwargs = kwargs
        if self.add_messages_error:
            raise self.add_messages_error

    async def search_user_mem(self, *args, **kwargs):
        self.search_user_mem_called = True
        self.search_user_mem_kwargs = kwargs
        if self.search_user_mem_error:
            raise self.search_user_mem_error
        return self.search_user_mem_result

    async def search_user_history_summary(self, *args, **kwargs):
        self.search_user_history_summary_called = True
        self.search_user_history_summary_kwargs = kwargs
        if self.search_user_history_summary_error:
            raise self.search_user_history_summary_error
        return self.search_user_history_summary_result


class MockContext:
    """Mock class for ModelContext."""

    pass


@pytest.fixture
def fake_session():
    """Create a fake Session for testing."""
    return Session(NodeSession(WorkflowSession(), "test_component"))


class TestMemoryWriteComponent:
    """Test cases for MemoryWriteComponent."""

    @pytest.mark.asyncio
    async def test_memory_write_success(self, fake_session):
        """
        Test successful memory write operation.
        Verifies that messages are correctly written to long-term memory
        and the output contains success=True.
        """
        mock_memory = MockMemory()

        config = MemoryWriteCompConfig(
            memory=mock_memory,
            scope_id="test_scope",
            user_id="test_user",
            session_id="test_session",
            gen_mem=True,
        )
        component = MemoryWriteComponent(config)
        executable = component.to_executable()

        context = MockContext()

        messages: list[BaseMessage] = [
            UserMessage(content="Hello"),
            AssistantMessage(content="Hi there!"),
        ]
        inputs = {"messages": messages}

        result = await executable.invoke(inputs, fake_session, context)

        assert result["success"] is True
        assert mock_memory.add_messages_called is True

    @pytest.mark.asyncio
    async def test_memory_write_with_timestamp(self, fake_session):
        """
        Test memory write operation with custom timestamp.
        Verifies that the timestamp parameter is correctly passed to add_messages.
        """

        mock_memory = MockMemory()

        config = MemoryWriteCompConfig(memory=mock_memory)
        component = MemoryWriteComponent(config)
        executable = component.to_executable()

        context = MockContext()

        timestamp = datetime.now(tz=timezone.utc)
        messages: list[BaseMessage] = [UserMessage(content="Test message")]
        inputs = {"messages": messages, "timestamp": timestamp}

        result = await executable.invoke(inputs, fake_session, context)

        assert result["success"] is True
        assert mock_memory.add_messages_kwargs["timestamp"] == timestamp

    @pytest.mark.asyncio
    async def test_memory_write_empty_messages_error(self, fake_session):
        """
        Test that empty messages list raises appropriate error.
        Verifies COMPONENT_MEMORY_WRITE_INPUT_PARAM_ERROR is raised
        when messages list is empty.
        """
        mock_memory = MockMemory()

        config = MemoryWriteCompConfig(memory=mock_memory)
        component = MemoryWriteComponent(config)
        executable = component.to_executable()

        context = MockContext()

        inputs = {"messages": []}

        with pytest.raises(BaseError) as exc_info:
            await executable.invoke(inputs, fake_session, context)

        assert exc_info.value.code == StatusCode.COMPONENT_MEMORY_WRITE_INPUT_PARAM_ERROR.code
        assert "Messages list cannot be empty" in str(exc_info.value.message)

    @staticmethod
    def test_memory_write_missing_messages_error():
        """
        Test that missing messages field raises validation error.
        Verifies COMPONENT_MEMORY_WRITE_INPUT_PARAM_ERROR is raised
        when messages field is not provided.
        """
        mock_memory = MockMemory()
        config = MemoryWriteCompConfig(memory=mock_memory)
        executable = MemoryWriteExecutable(config)

        with pytest.raises(BaseError) as exc_info:
            MemoryWriteExecutable.validate_inputs({})

        assert exc_info.value.code == StatusCode.COMPONENT_MEMORY_WRITE_INPUT_PARAM_ERROR.code

    @pytest.mark.asyncio
    async def test_memory_write_invoke_call_failed(self, fake_session):
        """
        Test that memory write failure raises appropriate error.
        Verifies COMPONENT_MEMORY_WRITE_INVOKE_CALL_FAILED is raised
        when add_messages throws an exception.
        """
        mock_memory = MockMemory()
        mock_memory.add_messages_error = Exception("DB connection failed")

        config = MemoryWriteCompConfig(memory=mock_memory)
        component = MemoryWriteComponent(config)
        executable = component.to_executable()

        context = MockContext()

        messages: list[BaseMessage] = [UserMessage(content="Test")]
        inputs = {"messages": messages}

        with pytest.raises(BaseError) as exc_info:
            await executable.invoke(inputs, fake_session, context)

        assert exc_info.value.code == StatusCode.COMPONENT_MEMORY_WRITE_INVOKE_CALL_FAILED.code
        assert "Memory write call failed" in str(exc_info.value.message)


class TestMemoryRetrievalComponent:
    """Test cases for MemoryRetrievalComponent."""

    @pytest.mark.asyncio
    async def test_memory_retrieval_success(self, fake_session):
        """
        Test successful memory retrieval operation.
        Verifies that search results are correctly returned
        with mem_info and score fields.
        """
        mock_mem_result = MemResult(
            mem_info=MemInfo(mem_id="mem_1", content="Test memory", type=MemoryType.USER_PROFILE),
            score=0.85,
        )
        mock_summary_result = MemResult(
            mem_info=MemInfo(mem_id="summary_1", content="Test summary", type=MemoryType.SUMMARY),
            score=0.8,
        )
        mock_memory = MockMemory()
        mock_memory.search_user_mem_result = [mock_mem_result]
        mock_memory.search_user_history_summary_result = [mock_summary_result]

        config = MemoryRetrievalCompConfig(
            memory=mock_memory,
            scope_id="test_scope",
            user_id="test_user",
            threshold=0.3,
        )
        component = MemoryRetrievalComponent(config)
        executable = component.to_executable()

        context = MockContext()

        inputs = {"query": "What is my name?", "top_k": 5}

        result = await executable.invoke(inputs, fake_session, context)

        assert "fragment_memory_results" in result
        assert "summary_results" in result
        assert len(result["fragment_memory_results"]) == 1
        assert result["fragment_memory_results"][0]["mem_info"]["mem_id"] == "mem_1"
        assert result["fragment_memory_results"][0]["score"] == 0.85
        assert len(result["summary_results"]) == 1
        assert result["summary_results"][0]["mem_info"]["mem_id"] == "summary_1"
        assert result["summary_results"][0]["score"] == 0.8

    @pytest.mark.asyncio
    async def test_memory_retrieval_multiple_results(self, fake_session):
        """
        Test memory retrieval with multiple results.
        Verifies that multiple search results are correctly returned
        and config parameters (top_k, threshold) are passed correctly.
        """
        mock_mem_results = [
            MemResult(
                mem_info=MemInfo(mem_id="mem_1", content="Memory 1", type=MemoryType.USER_PROFILE),
                score=0.9,
            ),
            MemResult(
                mem_info=MemInfo(mem_id="mem_2", content="Memory 2", type=MemoryType.USER_PROFILE),
                score=0.7,
            ),
        ]
        mock_summary_results = [
            MemResult(
                mem_info=MemInfo(mem_id="summary_1", content="Summary 1", type=MemoryType.SUMMARY),
                score=0.88,
            ),
        ]
        mock_memory = MockMemory()
        mock_memory.search_user_mem_result = mock_mem_results
        mock_memory.search_user_history_summary_result = mock_summary_results

        config = MemoryRetrievalCompConfig(memory=mock_memory, threshold=0.5)
        component = MemoryRetrievalComponent(config)
        executable = component.to_executable()

        context = MockContext()

        inputs = {"query": "test query", "top_k": 10}

        result = await executable.invoke(inputs, fake_session, context)

        assert len(result["fragment_memory_results"]) == 2
        assert len(result["summary_results"]) == 1
        assert mock_memory.search_user_mem_called is True
        assert mock_memory.search_user_history_summary_called is True
        assert mock_memory.search_user_mem_kwargs["num"] == 10
        assert mock_memory.search_user_mem_kwargs["threshold"] == 0.5

    @pytest.mark.asyncio
    async def test_memory_retrieval_empty_results(self, fake_session):
        """
        Test memory retrieval with no matching results.
        Verifies that empty list is returned when no memories match the query.
        """
        mock_memory = MockMemory()
        mock_memory.search_user_mem_result = []
        mock_memory.search_user_history_summary_result = []

        config = MemoryRetrievalCompConfig(memory=mock_memory)
        component = MemoryRetrievalComponent(config)
        executable = component.to_executable()

        context = MockContext()

        inputs = {"query": "nonexistent query"}

        result = await executable.invoke(inputs, fake_session, context)

        assert result["fragment_memory_results"] == []
        assert result["summary_results"] == []

    @pytest.mark.asyncio
    async def test_memory_retrieval_empty_query_error(self, fake_session):
        """
        Test that empty query string raises appropriate error.
        Verifies COMPONENT_MEMORY_RETRIEVAL_INPUT_PARAM_ERROR is raised
        when query is whitespace only.
        """
        mock_memory = MockMemory()
        mock_memory.search_user_mem_result = []

        config = MemoryRetrievalCompConfig(memory=mock_memory)
        component = MemoryRetrievalComponent(config)
        executable = component.to_executable()

        context = MockContext()

        inputs = {"query": "   "}

        with pytest.raises(BaseError) as exc_info:
            await executable.invoke(inputs, fake_session, context)

        assert exc_info.value.code == StatusCode.COMPONENT_MEMORY_RETRIEVAL_INPUT_PARAM_ERROR.code
        assert "Query must be a non-empty string" in str(exc_info.value.message)

    @staticmethod
    def test_memory_retrieval_missing_query_error():
        """
        Test that missing query field raises validation error.
        Verifies COMPONENT_MEMORY_RETRIEVAL_INPUT_PARAM_ERROR is raised
        when query field is not provided.
        """
        mock_memory = MockMemory()
        config = MemoryRetrievalCompConfig(memory=mock_memory)
        executable = MemoryRetrievalExecutable(config)

        with pytest.raises(BaseError) as exc_info:
            MemoryRetrievalExecutable.validate_inputs({})

        assert exc_info.value.code == StatusCode.COMPONENT_MEMORY_RETRIEVAL_INPUT_PARAM_ERROR.code

    @pytest.mark.asyncio
    async def test_memory_retrieval_invoke_call_failed(self, fake_session):
        """
        Test that memory retrieval failure raises appropriate error.
        Verifies COMPONENT_MEMORY_RETRIEVAL_INVOKE_CALL_FAILED is raised
        when search_user_mem throws an exception.
        """
        mock_memory = MockMemory()
        mock_memory.search_user_mem_error = Exception("Search failed")

        config = MemoryRetrievalCompConfig(memory=mock_memory)
        component = MemoryRetrievalComponent(config)
        executable = component.to_executable()

        context = MockContext()

        inputs = {"query": "test query"}

        with pytest.raises(BaseError) as exc_info:
            await executable.invoke(inputs, fake_session, context)

        assert exc_info.value.code == StatusCode.COMPONENT_MEMORY_RETRIEVAL_INVOKE_CALL_FAILED.code
        assert "Memory retrieval call failed" in str(exc_info.value.message)
