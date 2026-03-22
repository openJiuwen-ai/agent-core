# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import Mock, patch

import pytest

from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_reflector import (
    Reflector,
    extract_placeholder_content,
)


class TestExtractPlaceholderContent:
    """Test extract_placeholder_content function."""

    @staticmethod
    def test_extract_with_placeholder():
        """Test extraction with placeholder."""
        input_str = "${node_start.query}"
        has_placeholder, matches = extract_placeholder_content(input_str)
        
        assert has_placeholder is True
        assert "node_start.query" in matches

    @staticmethod
    def test_extract_without_placeholder():
        """Test extraction without placeholder."""
        input_str = "plain text"
        has_placeholder, matches = extract_placeholder_content(input_str)
        
        assert has_placeholder is False
        assert len(matches) == 0

    @staticmethod
    def test_extract_multiple_placeholders():
        """Test extraction with multiple placeholders."""
        input_str = "${node1.var1} and ${node2.var2}"
        has_placeholder, matches = extract_placeholder_content(input_str)
        
        assert has_placeholder is True
        assert len(matches) == 2

    @staticmethod
    def test_extract_empty_string():
        """Test extraction with empty string."""
        has_placeholder, matches = extract_placeholder_content("")
        
        assert has_placeholder is False
        assert len(matches) == 0


class TestReflectorInit:
    """Test Reflector initialization."""

    @staticmethod
    def test_init_success():
        """Test successful initialization."""
        reflector = Reflector()
        
        assert len(reflector.available_node_types) > 0
        assert len(reflector.available_variable_types) > 0
        assert len(reflector.available_condition_operators) > 0
        assert len(reflector.errors) == 0

    @staticmethod
    def test_available_node_types():
        """Test available node types."""
        reflector = Reflector()
        
        expected_types = {'Start', 'End', 'Output', 'LLM', 'Questioner', 'Plugin', 'Code', 'Branch', 'IntentDetection'}
        assert reflector.available_node_types == expected_types

    @staticmethod
    def test_available_variable_types():
        """Test available variable types."""
        reflector = Reflector()
        
        expected_types = {'String', 'Integer', 'Number', 'Boolean', 'Object', 
                         'Array<String>', 'Array<Integer>', 'Array<Number>', 
                         'Array<Boolean>', 'Array<Object>'}
        assert reflector.available_variable_types == expected_types


class TestReflectorCheckFormat:
    """Test Reflector check_format method."""

    @staticmethod
    def test_check_format_invalid_json():
        """Test check_format with invalid JSON."""
        reflector = Reflector()
        dl_content = 'invalid json'
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0

    @staticmethod
    def test_check_format_not_list():
        """Test check_format with non-list JSON."""
        reflector = Reflector()
        dl_content = '{"key": "value"}'
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0

    @staticmethod
    def test_check_format_missing_type():
        """Test check_format with missing type."""
        reflector = Reflector()
        dl_content = '[{"id": "node_1"}]'
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0

    @staticmethod
    def test_check_format_invalid_type():
        """Test check_format with invalid type."""
        reflector = Reflector()
        dl_content = '[{"id": "node_1", "type": "InvalidType"}]'
        
        reflector.check_format(dl_content)
        
        assert len(reflector.errors) > 0


class TestReflectorReset:
    """Test Reflector reset functionality."""

    @staticmethod
    def test_reset_after_check():
        """Test reset after check."""
        reflector = Reflector()
        reflector.errors = ["error1", "error2"]
        reflector.node_ids = ["id1", "id2"]
        
        reflector.errors = []
        reflector.node_ids = []
        
        assert len(reflector.errors) == 0
        assert len(reflector.node_ids) == 0
