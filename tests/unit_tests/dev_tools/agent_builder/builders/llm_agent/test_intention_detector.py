# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.intention_detector import IntentionDetector


class TestIntentionDetector:
    @pytest.fixture
    def detector(self):
        mock_llm = Mock()
        return IntentionDetector(mock_llm)

    @staticmethod
    def test_extract_intent_with_json_block(detector):
        input_text = '```json\n{"need_refined": true}\n```'
        result = detector.extract_intent(input_text)
        assert result == {"need_refined": True}

    @staticmethod
    def test_extract_intent_without_json_block(detector):
        input_text = '{"need_refined": false}'
        result = detector.extract_intent(input_text)
        assert result == {"need_refined": False}

    @staticmethod
    def test_extract_intent_with_multiline_json(detector):
        input_text = '```json\n{\n  "need_refined": true,\n  "reason": "test"\n}\n```'
        result = detector.extract_intent(input_text)
        assert result["need_refined"] is True
        assert result["reason"] == "test"

    @staticmethod
    def test_detect_refine_intent_empty_query(detector):
        result = detector.detect_refine_intent("", "some config")
        assert result is False

    @staticmethod
    def test_detect_refine_intent_none_query(detector):
        result = detector.detect_refine_intent(None, "some config")
        assert result is False

    @staticmethod
    @patch('asyncio.run')
    def test_detect_refine_intent_returns_true(mock_run, detector):
        mock_response = Mock()
        mock_response.content = '```json\n{"need_refined": true}\n```'
        
        mock_run.return_value = mock_response
        
        result = detector.detect_refine_intent("修改配置", "current config")
        
        assert result is True

    @staticmethod
    @patch('asyncio.run')
    def test_detect_refine_intent_returns_false(mock_run, detector):
        mock_response = Mock()
        mock_response.content = '```json\n{"need_refined": false}\n```'
        
        mock_run.return_value = mock_response
        
        result = detector.detect_refine_intent("确认", "current config")
        
        assert result is False

    @staticmethod
    @patch('asyncio.run')
    def test_detect_refine_intent_handles_exception(mock_run, detector):
        mock_run.side_effect = Exception("Test error")
        
        with pytest.raises(Exception):
            detector.detect_refine_intent("test query", "config")
