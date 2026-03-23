# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for Workflow CycleChecker module.

Tests CycleChecker integration with LLM.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.cycle_checker import CycleChecker


class TestCycleCheckerIntegration:
    """Test CycleChecker integration."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def cycle_checker(self, mock_llm):
        return CycleChecker(mock_llm)

    @staticmethod
    def test_cycle_checker_initialization(cycle_checker, mock_llm):
        """Test CycleChecker initialization."""
        assert cycle_checker.llm == mock_llm

    @staticmethod
    def test_parse_cycle_result_json_no_cycle(cycle_checker):
        """Test parse_cycle_result_json with no cycle."""
        input_str = '{"need_refined": false, "loop_desc": ""}'
        
        need_refined, loop_desc = CycleChecker.parse_cycle_result_json(input_str)
        
        assert need_refined is False
        assert loop_desc == ""

    @staticmethod
    def test_parse_cycle_result_json_with_cycle(cycle_checker):
        """Test parse_cycle_result_json with cycle."""
        input_str = '{"need_refined": true, "loop_desc": "Found cycle"}'
        
        need_refined, loop_desc = CycleChecker.parse_cycle_result_json(input_str)
        
        assert need_refined is True
        assert loop_desc == "Found cycle"

    @staticmethod
    def test_parse_cycle_result_json_with_markdown(cycle_checker):
        """Test parse_cycle_result_json with markdown."""
        input_str = '```json\n{"need_refined": true, "loop_desc": "Cycle detected"}\n```'
        
        need_refined, loop_desc = CycleChecker.parse_cycle_result_json(input_str)
        
        assert need_refined is True
        assert loop_desc == "Cycle detected"


class TestCycleCheckerMermaidCode:
    """Test CycleChecker with Mermaid code."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def cycle_checker(self, mock_llm):
        return CycleChecker(mock_llm)

    @staticmethod
    def test_check_mermaid_cycle_simple(cycle_checker, mock_llm):
        """Test check_mermaid_cycle with simple graph."""
        mock_llm.invoke.return_value = Mock(content='{"need_refined": false}')
        
        mermaid_code = "graph TD\n  A --> B"
        
        result = cycle_checker.check_mermaid_cycle(mermaid_code)
        
        assert result is not None
        mock_llm.invoke.assert_called_once()

    @staticmethod
    def test_check_and_parse_integration(cycle_checker, mock_llm):
        """Test check_and_parse integration."""
        mock_llm.invoke.return_value = Mock(content='{"need_refined": false, "loop_desc": ""}')
        
        mermaid_code = "graph TD\n  A --> B"
        
        need_refined, loop_desc = cycle_checker.check_and_parse(mermaid_code)
        
        assert need_refined is False
        assert loop_desc == ""
