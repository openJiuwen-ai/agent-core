# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for LLM Agent Generator module.

Tests Generator integration with LLM.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.generator import Generator


class TestGeneratorIntegration:
    """Test Generator integration."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def generator(self, mock_llm):
        return Generator(mock_llm)

    @staticmethod
    def test_generator_initialization(generator, mock_llm):
        """Test Generator initialization."""
        assert generator.llm == mock_llm

    @staticmethod
    def test_extract_elements_constant():
        """Test EXTRACT_ELEMENTS constant."""
        assert "name" in Generator.EXTRACT_ELEMENTS
        assert "description" in Generator.EXTRACT_ELEMENTS
        assert "prompt" in Generator.EXTRACT_ELEMENTS
        assert "opening_remarks" in Generator.EXTRACT_ELEMENTS
        assert "question" in Generator.EXTRACT_ELEMENTS

    @staticmethod
    def test_parse_info_empty(generator):
        """Test _parse_info with empty content."""
        result = Generator.parse_info("")
        
        assert isinstance(result, dict)
        assert "name" in result
        assert "description" in result
        assert "prompt" in result

    @staticmethod
    def test_parse_info_with_content(generator):
        """Test _parse_info with content."""
        content = """
        <角色名称>Test Agent</角色名称>
        <角色描述>Test Description</角色描述>
        <提示词>Test Prompt</提示词>
        <智能体开场白>Hello</智能体开场白>
        <预置问题>Question?</预置问题>
        """
        
        result = Generator.parse_info(content)
        
        assert result["name"] == "Test Agent"
        assert result["description"] == "Test Description"
        assert result["prompt"] == "Test Prompt"
        assert result["opening_remarks"] == "Hello"
        assert result["question"] == "Question?"

    @staticmethod
    def test_parse_info_with_quotes(generator):
        """Test _parse_info with quoted content."""
        content = '<角色名称>"Quoted Name"</角色名称>'
        
        result = Generator.parse_info(content)
        
        assert result["name"] == "Quoted Name"


class TestGeneratorExtractElements:
    """Test Generator extract elements."""

    @staticmethod
    def test_name_element():
        """Test name element mapping."""
        assert Generator.EXTRACT_ELEMENTS["name"] == "角色名称"

    @staticmethod
    def test_description_element():
        """Test description element mapping."""
        assert Generator.EXTRACT_ELEMENTS["description"] == "角色描述"

    @staticmethod
    def test_prompt_element():
        """Test prompt element mapping."""
        assert Generator.EXTRACT_ELEMENTS["prompt"] == "提示词"

    @staticmethod
    def test_opening_remarks_element():
        """Test opening_remarks element mapping."""
        assert Generator.EXTRACT_ELEMENTS["opening_remarks"] == "智能体开场白"

    @staticmethod
    def test_question_element():
        """Test question element mapping."""
        assert Generator.EXTRACT_ELEMENTS["question"] == "预置问题"
