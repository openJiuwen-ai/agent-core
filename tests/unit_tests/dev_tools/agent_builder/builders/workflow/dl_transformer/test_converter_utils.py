# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from unittest.mock import Mock, patch

import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.converter_utils import ConverterUtils


class TestConverterUtilsGenerateNodeId:
    """Test ConverterUtils.generate_node_id method."""

    @staticmethod
    def test_generate_node_id_with_prefix():
        """Test generate_node_id with prefix."""
        node_id = ConverterUtils.generate_node_id("node")
        
        assert node_id.startswith("node_")
        assert len(node_id) > 5

    @staticmethod
    def test_generate_node_id_uniqueness():
        """Test generate_node_id produces unique IDs."""
        id1 = ConverterUtils.generate_node_id("node")
        id2 = ConverterUtils.generate_node_id("node")
        
        assert id1 != id2

    @staticmethod
    def test_generate_node_id_with_empty_prefix():
        """Test generate_node_id with empty prefix."""
        node_id = ConverterUtils.generate_node_id("")
        
        assert node_id.startswith("_")


class TestConverterUtilsExtractVariable:
    """Test ConverterUtils.extract_variable method."""

    @staticmethod
    def test_extract_variable_success():
        """Test successful extraction."""
        result = ConverterUtils.extract_variable("${node_start.query}")
        
        assert result == ("node_start", "query")

    @staticmethod
    def test_extract_variable_invalid_format():
        """Test extraction with invalid format."""
        result = ConverterUtils.extract_variable("invalid")
        
        assert result is None

    @staticmethod
    def test_extract_variable_empty_string():
        """Test extraction with empty string."""
        result = ConverterUtils.extract_variable("")
        
        assert result is None

    @staticmethod
    def test_extract_variable_missing_braces():
        """Test extraction with missing braces."""
        result = ConverterUtils.extract_variable("node_start.query")
        
        assert result is None


class TestConverterUtilsConvertRefVariable:
    """Test ConverterUtils.convert_ref_variable method."""

    @staticmethod
    def test_convert_ref_variable_success():
        """Test successful conversion."""
        result = ConverterUtils.convert_ref_variable("${node_start.query}")
        
        assert result["type"] == "ref"
        assert result["content"] == ["node_start", "query"]

    @staticmethod
    def test_convert_ref_variable_nested():
        """Test conversion with nested variable."""
        result = ConverterUtils.convert_ref_variable("${node_llm.output_of_result}")
        
        assert result["type"] == "ref"
        assert result["content"] == ["node_llm", "result", "output"]

    @staticmethod
    def test_convert_ref_variable_invalid():
        """Test conversion with invalid expression."""
        with pytest.raises(ValidationError):
            ConverterUtils.convert_ref_variable("invalid")


class TestConverterUtilsConvertLlmParam:
    """Test ConverterUtils.convert_llm_param method."""

    @staticmethod
    def test_convert_llm_param_success():
        """Test successful conversion."""
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
        """Test conversion contains model config."""
        result = ConverterUtils.convert_llm_param("system", "user")
        
        assert result["mode"]["id"] == "52"
        assert result["mode"]["name"] == "siliconf-qwen3-8b"


class TestConverterUtilsConvertToDict:
    """Test ConverterUtils.convert_to_dict method."""

    @staticmethod
    def test_convert_to_dict_with_dataclass():
        """Test conversion with dataclass."""
        from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import Position
        
        position = Position(x=100.0, y=200.0)
        result = ConverterUtils.convert_to_dict(position)
        
        assert result["x"] == 100.0
        assert result["y"] == 200.0

    @staticmethod
    def test_convert_to_dict_with_dict():
        """Test conversion with dict."""
        data = {"key": "value", "none_key": None}
        result = ConverterUtils.convert_to_dict(data)
        
        assert result["key"] == "value"
        assert "none_key" not in result

    @staticmethod
    def test_convert_to_dict_with_list():
        """Test conversion with list."""
        data = [{"key": "value"}, None, {"key2": "value2"}]
        result = ConverterUtils.convert_to_dict(data)
        
        assert len(result) == 2
        assert result[0]["key"] == "value"
        assert result[1]["key2"] == "value2"

    @staticmethod
    def test_convert_to_dict_with_none():
        """Test conversion with None."""
        result = ConverterUtils.convert_to_dict(None)
        
        assert result == {}

    @staticmethod
    def test_convert_to_dict_removes_none_values():
        """Test conversion removes None values."""
        from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.models import InputsField
        
        inputs = InputsField(llm_param=None, code=None)
        result = ConverterUtils.convert_to_dict(inputs)
        
        assert "llm_param" not in result
        assert "code" not in result


class TestConverterUtilsLlmModelConfig:
    """Test ConverterUtils LLM_MODEL_CONFIG constant."""

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
