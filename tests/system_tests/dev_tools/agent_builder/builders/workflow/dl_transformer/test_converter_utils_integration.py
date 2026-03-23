# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
System tests for DL Transformer ConverterUtils module.

Tests ConverterUtils integration.
"""
import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class TestConverterUtilsIntegration:
    """Test ConverterUtils integration."""

    @staticmethod
    def test_generate_node_id_uniqueness():
        """Test generate_node_id produces unique IDs."""
        ids = [ConverterUtils.generate_node_id("node") for _ in range(100)]
        
        assert len(set(ids)) == 100

    @staticmethod
    def test_generate_node_id_with_prefix():
        """Test generate_node_id with prefix."""
        node_id = ConverterUtils.generate_node_id("start")
        
        assert node_id.startswith("start_")

    @staticmethod
    def test_extract_variable_valid():
        """Test extract_variable with valid input."""
        result = ConverterUtils.extract_variable("${node_start.query}")
        
        assert result == ("node_start", "query")

    @staticmethod
    def test_extract_variable_invalid():
        """Test extract_variable with invalid input."""
        result = ConverterUtils.extract_variable("invalid")
        
        assert result is None

    @staticmethod
    def test_convert_ref_variable_valid():
        """Test convert_ref_variable with valid input."""
        result = ConverterUtils.convert_ref_variable("${node_start.query}")
        
        assert result["type"] == "ref"
        assert result["content"] == ["node_start", "query"]

    @staticmethod
    def test_convert_ref_variable_nested():
        """Test convert_ref_variable with nested variable."""
        result = ConverterUtils.convert_ref_variable("${node_llm.output_of_result}")
        
        assert result["type"] == "ref"
        assert result["content"] == ["node_llm", "result", "output"]

    @staticmethod
    def test_convert_ref_variable_invalid():
        """Test convert_ref_variable with invalid input."""
        with pytest.raises(ValidationError):
            ConverterUtils.convert_ref_variable("invalid")


class TestConverterUtilsConvertLlmParam:
    """Test ConverterUtils convert_llm_param method."""

    @staticmethod
    def test_convert_llm_param_basic():
        """Test basic convert_llm_param."""
        result = ConverterUtils.convert_llm_param(
            system_prompt="You are helpful",
            user_prompt="{{query}}"
        )
        
        assert result["systemPrompt"]["type"] == "template"
        assert result["systemPrompt"]["content"] == "You are helpful"
        assert result["prompt"]["type"] == "template"
        assert result["prompt"]["content"] == "{{query}}"
        assert "mode" in result

    @staticmethod
    def test_convert_llm_param_contains_model_config():
        """Test convert_llm_param contains model config."""
        result = ConverterUtils.convert_llm_param("system", "user")
        
        assert "mode" in result
        assert "id" in result["mode"]
        assert "name" in result["mode"]


class TestConverterUtilsConvertToDict:
    """Test ConverterUtils convert_to_dict method."""

    @staticmethod
    def test_convert_to_dict_with_dict():
        """Test convert_to_dict with dict."""
        data = {"key": "value", "none_key": None}
        result = ConverterUtils.convert_to_dict(data)
        
        assert result["key"] == "value"
        assert "none_key" not in result

    @staticmethod
    def test_convert_to_dict_with_list():
        """Test convert_to_dict with list."""
        data = [{"key": "value"}, None, {"key2": "value2"}]
        result = ConverterUtils.convert_to_dict(data)
        
        assert len(result) == 2
        assert result[0]["key"] == "value"
        assert result[1]["key2"] == "value2"

    @staticmethod
    def test_convert_to_dict_with_none():
        """Test convert_to_dict with None."""
        result = ConverterUtils.convert_to_dict(None)
        
        assert result == {}

    @staticmethod
    def test_convert_to_dict_with_dataclass():
        """Test convert_to_dict with dataclass."""
        from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import Position
        
        position = Position(x=100.0, y=200.0)
        result = ConverterUtils.convert_to_dict(position)
        
        assert result["x"] == 100.0
        assert result["y"] == 200.0


class TestConverterUtilsLlmModelConfig:
    """Test ConverterUtils LLM_MODEL_CONFIG."""

    @staticmethod
    def test_llm_model_config_exists():
        """Test LLM_MODEL_CONFIG exists."""
        assert hasattr(ConverterUtils, 'LLM_MODEL_CONFIG')

    @staticmethod
    def test_llm_model_config_has_required_fields():
        """Test LLM_MODEL_CONFIG has required fields."""
        assert "id" in ConverterUtils.LLM_MODEL_CONFIG
        assert "name" in ConverterUtils.LLM_MODEL_CONFIG
        assert "type" in ConverterUtils.LLM_MODEL_CONFIG
