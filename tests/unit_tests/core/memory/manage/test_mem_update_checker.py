#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for memory update checker module.

This module contains tests for MemUpdateChecker class that detects redundancy
and conflicts between memories using LLM analysis.
"""

from unittest.mock import Mock, AsyncMock, patch
import pytest

from openjiuwen.core.memory.manage.update.mem_update_checker import (
    MemUpdateChecker,
    CheckResult,
    MemoryStatus,
    MemoryActionItem,
    MemCheckItem,
    _format_input,
)
from openjiuwen.core.foundation.llm import Model


class TestMemUpdateChecker:
    """Test cases for MemUpdateChecker class."""

    @pytest.fixture
    def checker(self):
        """Create a MemUpdateChecker instance for testing."""
        return MemUpdateChecker()

    @pytest.fixture
    def mock_model_client(self):
        """Create a mock model client for testing."""
        mock_client = Mock(spec=Model)
        mock_client.invoke = AsyncMock()
        return mock_client

    @pytest.fixture
    def mock_prompt_applier(self):
        """Create a mock prompt applier for testing."""
        with patch('openjiuwen.core.memory.manage.update.mem_update_checker.PromptApplier') as mock:
            mock_instance = Mock()
            mock_instance.apply = Mock(return_value="mocked prompt")
            mock.return_value = mock_instance
            yield mock_instance

    @pytest.mark.asyncio
    async def test_check_with_no_model(self, checker):
        """Test check method when no model is provided."""
        new_memories = {"1": "I like reading"}
        old_memories = {"2": "I enjoy books"}
        base_chat_model = None

        results = await checker.check(new_memories, old_memories, base_chat_model)

        assert len(results) == 1
        assert results[0].id == "1"
        assert results[0].content == "I like reading"
        assert results[0].status == MemoryStatus.ADD

    @pytest.mark.asyncio
    async def test_check_with_duplicate_ids(self, checker, mock_model_client, mock_prompt_applier):
        """Test check method when new and old memories have duplicate IDs."""
        new_memories = {"1": "I like reading", "2": "I enjoy books"}
        old_memories = {"1": "I like reading", "3": "I love novels"}
        base_chat_model = mock_model_client

        # Mock successful LLM response
        mock_response = Mock()
        mock_response.content =\
            '[{"info_id": "1", "info_text": "I like reading", "result": "none", "related_infos": {}}]'
        mock_model_client.invoke.return_value = mock_response

        with patch('openjiuwen.core.memory.manage.update.mem_update_checker.JsonOutputParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser.parse = AsyncMock(return_value=[{
                "info_id": "1",
                "info_text": "I like reading",
                "result": "none",
                "related_infos": {}
            }])
            mock_parser_class.return_value = mock_parser

            results = await checker.check(new_memories, old_memories, base_chat_model)

            # Should log duplicate IDs but still process
            assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_check_with_redundant_result(self, checker, mock_model_client, mock_prompt_applier):
        """Test check method when LLM returns redundant result."""
        new_memories = {"1": "I like reading"}
        old_memories = {"2": "I enjoy books"}
        base_chat_model = mock_model_client

        # Mock successful LLM response with redundant result
        mock_response = Mock()
        mock_response.content = ('[{"info_id": "1", "info_text": "I like reading", "result": "redundant",'
                                 ' "related_infos": {"2": "I enjoy books"}}]')
        mock_model_client.invoke.return_value = mock_response

        with patch('openjiuwen.core.memory.manage.update.mem_update_checker.JsonOutputParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser.parse = AsyncMock(return_value=[{
                "info_id": "1",
                "info_text": "I like reading",
                "result": "redundant",
                "related_infos": {"2": "I enjoy books"}
            }])
            mock_parser_class.return_value = mock_parser

            results = await checker.check(new_memories, old_memories, base_chat_model)

            # Redundant memories should not be included in results
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_check_with_conflicting_result(self, checker, mock_model_client, mock_prompt_applier):
        """Test check method when LLM returns conflicting result."""
        new_memories = {"1": "I like reading"}
        old_memories = {"2": "I hate books"}
        base_chat_model = mock_model_client

        # Mock successful LLM response with conflicting result
        mock_response = Mock()
        mock_response.content = ('[{"info_id": "1", "info_text": "I like reading", "result": "conflicting",'
                                 ' "related_infos": {"2": "I hate books"}}]')
        mock_model_client.invoke.return_value = mock_response

        with patch('openjiuwen.core.memory.manage.update.mem_update_checker.JsonOutputParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser.parse = AsyncMock(return_value=[{
                "info_id": "1",
                "info_text": "I like reading",
                "result": "conflicting",
                "related_infos": {"2": "I hate books"}
            }])
            mock_parser_class.return_value = mock_parser

            results = await checker.check(new_memories, old_memories, base_chat_model)

            # Should include new memory with ADD status and old memory with DELETE status
            assert len(results) == 2
            new_memory_item = next(item for item in results if item.id == "1")
            old_memory_item = next(item for item in results if item.id == "2")

            assert new_memory_item.status == MemoryStatus.ADD
            assert old_memory_item.status == MemoryStatus.DELETE

    @pytest.mark.asyncio
    async def test_check_with_none_result(self, checker, mock_model_client, mock_prompt_applier):
        """Test check method when LLM returns none result (no conflict)."""
        new_memories = {"1": "I like reading"}
        old_memories = {"2": "I enjoy sports"}
        base_chat_model = mock_model_client

        # Mock successful LLM response with none result
        mock_response = Mock()
        mock_response.content =\
            '[{"info_id": "1", "info_text": "I like reading", "result": "none", "related_infos": {}}]'
        mock_model_client.invoke.return_value = mock_response

        with patch('openjiuwen.core.memory.manage.update.mem_update_checker.JsonOutputParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser.parse = AsyncMock(return_value=[{
                "info_id": "1",
                "info_text": "I like reading",
                "result": "none",
                "related_infos": {}
            }])
            mock_parser_class.return_value = mock_parser

            results = await checker.check(new_memories, old_memories, base_chat_model)

            # Should include new memory with ADD status
            assert len(results) == 1
            assert results[0].id == "1"
            assert results[0].status == MemoryStatus.ADD

    @pytest.mark.asyncio
    async def test_check_with_malformed_response(self, checker, mock_model_client, mock_prompt_applier):
        """Test check method when LLM returns malformed response."""
        new_memories = {"1": "I like reading"}
        old_memories = {"2": "I enjoy books"}
        base_chat_model = mock_model_client

        # Mock malformed LLM response
        mock_response = Mock()
        mock_response.content = 'invalid json'
        mock_model_client.invoke.return_value = mock_response

        with patch('openjiuwen.core.memory.manage.update.mem_update_checker.JsonOutputParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser.parse = AsyncMock(side_effect=ValueError("Parse error"))
            mock_parser_class.return_value = mock_parser

            results = await checker.check(new_memories, old_memories, base_chat_model)

            # Should return all new memories as ADD when parsing fails
            assert len(results) == 1
            assert results[0].id == "1"
            assert results[0].status == MemoryStatus.ADD

    @pytest.mark.asyncio
    async def test_check_with_single_object_response(self, checker, mock_model_client, mock_prompt_applier):
        """Test check method when LLM returns single object instead of list."""
        new_memories = {"1": "I like reading"}
        old_memories = {"2": "I enjoy books"}
        base_chat_model = mock_model_client

        # Mock LLM response with single object (not list)
        mock_response = Mock()
        mock_response.content =\
            '{"info_id": "1", "info_text": "I like reading", "result": "none", "related_infos": {}}'
        mock_model_client.invoke.return_value = mock_response

        with patch('openjiuwen.core.memory.manage.update.mem_update_checker.JsonOutputParser') as mock_parser_class:
            mock_parser = Mock()
            mock_parser.parse = AsyncMock(return_value={
                "info_id": "1",
                "info_text": "I like reading",
                "result": "none",
                "related_infos": {}
            })
            mock_parser_class.return_value = mock_parser

            results = await checker.check(new_memories, old_memories, base_chat_model)

            # Should handle single object response correctly
            assert len(results) == 1
            assert results[0].id == "1"
            assert results[0].status == MemoryStatus.ADD

    @staticmethod
    def test_format_input_function():
        """Test the _format_input helper function."""
        new_memories = {"1": "I like reading", "2": "I enjoy books"}
        old_memories = {"3": "I love novels", "4": "I hate sports"}

        new_str, old_str = _format_input(new_memories, old_memories)

        expected_new = "2: I enjoy books\n1: I like reading"
        expected_old = "3: I love novels\n4: I hate sports"

        assert new_str == expected_new
        assert old_str == expected_old

    @staticmethod
    def test_format_input_empty_dicts():
        """Test the _format_input function with empty dictionaries."""
        new_memories = {}
        old_memories = {}

        new_str, old_str = _format_input(new_memories, old_memories)

        assert new_str == ""
        assert old_str == ""

    @staticmethod
    def test_memory_action_item_creation():
        """Test MemoryActionItem model creation and validation."""
        item = MemoryActionItem(
            id="test_id",
            content="test content",
            status=MemoryStatus.ADD
        )

        assert item.id == "test_id"
        assert item.content == "test content"
        assert item.status == MemoryStatus.ADD

    @staticmethod
    def test_mem_check_item_creation():
        """Test MemCheckItem model creation and validation."""
        item = MemCheckItem(
            info_id="test_id",
            info_text="test content",
            result=CheckResult.NONE,
            related_infos={"old_id": "old content"}
        )

        assert item.info_id == "test_id"
        assert item.info_text == "test content"
        assert item.result == CheckResult.NONE
        assert item.related_infos == {"old_id": "old content"}

    @staticmethod
    def test_enum_values():
        """Test enum values are correctly defined."""
        assert CheckResult.REDUNDANT == "redundant"
        assert CheckResult.CONFLICTING == "conflicting"
        assert CheckResult.NONE == "none"

        assert MemoryStatus.ADD == "add"
        assert MemoryStatus.DELETE == "delete"
