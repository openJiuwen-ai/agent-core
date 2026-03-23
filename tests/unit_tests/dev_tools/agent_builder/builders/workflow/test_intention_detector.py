# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.intention_detector import IntentionDetector


class TestWorkflowIntentionDetector:
    @pytest.fixture
    def detector(self):
        mock_llm = Mock()
        return IntentionDetector(mock_llm)

    @staticmethod
    def test_format_dialog_history(detector):
        dialog_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "system", "content": "System message"}
        ]
        
        result = detector.format_dialog_history(dialog_history)
        
        assert "User: Hello" in result
        assert "Assistant: Hi there!" in result
        assert "System: System message" in result

    @staticmethod
    def test_format_dialog_history_empty(detector):
        result = detector.format_dialog_history([])
        assert result == ""

    @staticmethod
    def test_format_dialog_history_unknown_role(detector):
        dialog_history = [
            {"role": "unknown", "content": "Test"}
        ]
        
        result = detector.format_dialog_history(dialog_history)
        assert "User: Test" in result

    @staticmethod
    def test_extract_intent_with_json_block(detector):
        input_text = '```json\n{"has_instruction": true}\n```'
        result = IntentionDetector.extract_intent(input_text)
        assert result == {"has_instruction": True}

    @staticmethod
    def test_extract_intent_without_json_block(detector):
        input_text = '{"has_instruction": false}'
        result = IntentionDetector.extract_intent(input_text)
        assert result == {"has_instruction": False}

    @staticmethod
    @patch('asyncio.run')
    def test_detect_initial_instruction(mock_run, detector):
        mock_response = Mock()
        mock_response.content = '```json\n{"has_instruction": true}\n```'
        
        mock_run.return_value = mock_response
        
        result = detector.detect_initial_instruction([
            {"role": "user", "content": "创建一个数据处理工作流"}
        ])
        
        assert isinstance(result, bool)

    @staticmethod
    @patch('asyncio.run')
    def test_detect_refine_intent_true(mock_run, detector):
        mock_response = Mock()
        mock_response.content = '```json\n{"need_refined": true}\n```'
        
        mock_run.return_value = mock_response
        
        result = detector.detect_refine_intent(
            [{"role": "user", "content": "修改节点"}],
            "graph TD; A-->B"
        )
        
        assert result is True

    @staticmethod
    @patch('asyncio.run')
    def test_detect_refine_intent_false(mock_run, detector):
        mock_response = Mock()
        mock_response.content = '```json\n{"need_refined": false}\n```'
        
        mock_run.return_value = mock_response
        
        result = detector.detect_refine_intent(
            [{"role": "user", "content": "确认"}],
            "graph TD; A-->B"
        )
        
        assert result is False
