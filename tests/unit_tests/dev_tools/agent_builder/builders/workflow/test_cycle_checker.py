# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.cycle_checker import CycleChecker


class TestCycleChecker:
    @pytest.fixture
    def checker(self):
        mock_llm = Mock()
        return CycleChecker(mock_llm)

    @staticmethod
    def test_parse_cycle_result_json_with_cycle(checker):
        json_input = '```json\n{"need_refined": true, "loop_desc": "A->B->A"}\n```'
        
        need_refined, loop_desc = CycleChecker.parse_cycle_result_json(json_input)
        
        assert need_refined is True
        assert loop_desc == "A->B->A"

    @staticmethod
    def test_parse_cycle_result_json_no_cycle(checker):
        json_input = '```json\n{"need_refined": false, "loop_desc": ""}\n```'
        
        need_refined, loop_desc = CycleChecker.parse_cycle_result_json(json_input)
        
        assert need_refined is False
        assert loop_desc == ""

    @staticmethod
    def test_parse_cycle_result_json_without_code_block(checker):
        json_input = '{"need_refined": true, "loop_desc": "cycle detected"}'
        
        need_refined, loop_desc = CycleChecker.parse_cycle_result_json(json_input)
        
        assert need_refined is True
        assert loop_desc == "cycle detected"

    @staticmethod
    def test_parse_cycle_result_json_missing_keys(checker):
        json_input = '{"other_key": "value"}'
        
        need_refined, loop_desc = CycleChecker.parse_cycle_result_json(json_input)
        
        assert need_refined is False
        assert loop_desc == ""

    @staticmethod
    @patch('asyncio.run')
    def test_check_mermaid_cycle(mock_run, checker):
        mock_response = Mock()
        mock_response.content = '{"need_refined": false}'
        
        mock_run.return_value = mock_response
        
        result = checker.check_mermaid_cycle("graph TD; A-->B")
        
        assert result == '{"need_refined": false}'

    @staticmethod
    @patch('asyncio.run')
    def test_check_and_parse(mock_run, checker):
        mock_response = Mock()
        mock_response.content = '```json\n{"need_refined": true, "loop_desc": "A->B->C->A"}\n```'
        
        mock_run.return_value = mock_response
        
        need_refined, loop_desc = checker.check_and_parse("graph TD; A-->B-->C-->A")
        
        assert need_refined is True
        assert loop_desc == "A->B->C->A"
