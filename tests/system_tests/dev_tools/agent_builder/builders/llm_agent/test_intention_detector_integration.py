# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for LLM Agent IntentionDetector module.

Tests IntentionDetector integration with LLM.
"""
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.llm_agent.intention_detector import IntentionDetector


class TestIntentionDetectorIntegration:
    """Test IntentionDetector integration."""

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
    def test_extract_intent_with_json_block(intention_detector):
        """Test _extract_intent with JSON block."""
        input_str = '```json\n{"need_refined": true}\n```'
        
        result = intention_detector.extract_intent(input_str)
        
        assert isinstance(result, dict)
        assert result.get("need_refined") is True

    @staticmethod
    def test_extract_intent_without_json_block(intention_detector):
        """Test _extract_intent without JSON block."""
        input_str = '{"need_refined": false}'
        
        result = intention_detector.extract_intent(input_str)
        
        assert isinstance(result, dict)
        assert result.get("need_refined") is False


class TestIntentionDetectorDetectRefineIntent:
    """Test IntentionDetector detect_refine_intent method."""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock()
        llm.invoke = AsyncMock()
        return llm

    @pytest.fixture
    def intention_detector(self, mock_llm):
        return IntentionDetector(mock_llm)

    @staticmethod
    def test_detect_refine_intent_empty_query(intention_detector):
        """Test detect_refine_intent with empty query."""
        result = intention_detector.detect_refine_intent("", "config")
        
        assert result is False

    @staticmethod
    def test_detect_refine_intent_none_query(intention_detector):
        """Test detect_refine_intent with None query."""
        result = intention_detector.detect_refine_intent(None, "config")
        
        assert result is False
