# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for Workflow IntentionDetector module.

Tests Workflow IntentionDetector integration with LLM.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.intention_detector import IntentionDetector


class TestWorkflowIntentionDetectorIntegration:
    """Test Workflow IntentionDetector integration."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def intention_detector(self, mock_llm):
        return IntentionDetector(mock_llm)

    @staticmethod
    def test_intention_detector_initialization(intention_detector, mock_llm):
        """Test IntentionDetector initialization."""
        assert intention_detector.llm == mock_llm

    @staticmethod
    def test_detect_initial_instruction_empty_history(intention_detector):
        """Test detect_initial_instruction with empty history."""
        result = intention_detector.detect_initial_instruction([])
        
        assert result is False

    @staticmethod
    def test_detect_initial_instruction_with_history(intention_detector, mock_llm):
        """Test detect_initial_instruction with history."""
        mock_llm.invoke.return_value = Mock(content='{"provide_process": true}')
        
        dialog_history = [{"role": "user", "content": "create workflow"}]
        
        result = intention_detector.detect_initial_instruction(dialog_history)
        
        assert isinstance(result, bool)

    @staticmethod
    def test_detect_refine_intent_empty_history(intention_detector):
        """Test detect_refine_intent with empty history."""
        result = intention_detector.detect_refine_intent([], "mermaid code")
        
        assert result is False

    @staticmethod
    def test_detect_refine_intent_with_history(intention_detector, mock_llm):
        """Test detect_refine_intent with history."""
        mock_llm.invoke.return_value = Mock(content='{"need_refined": true}')
        
        dialog_history = [{"role": "user", "content": "modify workflow"}]
        
        result = intention_detector.detect_refine_intent(dialog_history, "graph TD")
        
        assert isinstance(result, bool)


class TestWorkflowIntentionDetectorExtractIntent:
    """Test Workflow IntentionDetector extract intent."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def intention_detector(self, mock_llm):
        return IntentionDetector(mock_llm)

    @staticmethod
    def test_extract_intent_with_json_block(intention_detector):
        """Test _extract_intent with JSON block."""
        input_str = '```json\n{"provide_process": true}\n```'
        
        result = intention_detector.extract_intent(input_str)
        
        assert isinstance(result, dict)
        assert result.get("provide_process") is True

    @staticmethod
    def test_extract_intent_without_json_block(intention_detector):
        """Test _extract_intent without JSON block."""
        input_str = '{"provide_process": false}'
        
        result = intention_detector.extract_intent(input_str)
        
        assert isinstance(result, dict)
        assert result.get("provide_process") is False


class TestWorkflowIntentionDetectorFormatDialogHistory:
    """Test Workflow IntentionDetector format dialog history."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def intention_detector(self, mock_llm):
        return IntentionDetector(mock_llm)

    @staticmethod
    def test_format_dialog_history_user(intention_detector):
        """Test _format_dialog_history with user message."""
        dialog_history = [{"role": "user", "content": "test message"}]
        
        result = intention_detector.format_dialog_history(dialog_history)
        
        assert "User: test message" in result

    @staticmethod
    def test_format_dialog_history_assistant(intention_detector):
        """Test _format_dialog_history with assistant message."""
        dialog_history = [{"role": "assistant", "content": "response"}]
        
        result = intention_detector.format_dialog_history(dialog_history)
        
        assert "Assistant: response" in result

    @staticmethod
    def test_format_dialog_history_mixed(intention_detector):
        """Test _format_dialog_history with mixed messages."""
        dialog_history = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"}
        ]
        
        result = intention_detector.format_dialog_history(dialog_history)
        
        assert "User: question" in result
        assert "Assistant: answer" in result
