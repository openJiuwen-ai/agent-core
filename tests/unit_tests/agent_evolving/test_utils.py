# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for TuneUtils utility functions."""

import pytest

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
from openjiuwen.agent_evolving.utils import TuneUtils
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, ToolCall, UserMessage
from openjiuwen.core.foundation.prompt import PromptTemplate


def create_tool_call(func_name: str, arguments: str, call_id: str = "call_id") -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        name=func_name,
        arguments=arguments,
    )


class TestValidateDigitalParameter:
    """Test validate_digital_parameter function."""

    @staticmethod
    def test_valid_boundary_values():
        """Test valid boundary values pass."""
        TuneUtils.validate_digital_parameter(0.0, "param", 0.0, 1.0)
        TuneUtils.validate_digital_parameter(1.0, "param", 0.0, 1.0)
        TuneUtils.validate_digital_parameter(0.5, "param", 0.0, 1.0)

    @staticmethod
    def test_invalid_below_lower():
        """Value below lower bound raises error."""
        with pytest.raises(ValidationError):
            TuneUtils.validate_digital_parameter(-0.1, "param", 0.0, 1.0)

    @staticmethod
    def test_invalid_above_upper():
        """Value above upper bound raises error."""
        with pytest.raises(ValidationError):
            TuneUtils.validate_digital_parameter(1.1, "param", 0.0, 1.0)

    @staticmethod
    def test_custom_param_name():
        """Param name appears in error message."""
        try:
            TuneUtils.validate_digital_parameter(100, "custom_param", 0, 10)
        except ValidationError as e:
            assert "custom_param" in str(e)

    @staticmethod
    def test_negative_bounds():
        """Works with negative bounds."""
        TuneUtils.validate_digital_parameter(-5, "param", -10, 0)

    @staticmethod
    def test_integer_values():
        """Works with integer values."""
        TuneUtils.validate_digital_parameter(5, "param", 1, 10)


class TestParseJsonFromLlmResponse:
    """Test parse_json_from_llm_response function."""

    @staticmethod
    def test_valid_json_block():
        """Valid JSON in code block."""
        response = '```json\n{"result": true, "score": 0.9}\n```'
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result == {"result": True, "score": 0.9}

    @staticmethod
    def test_json_with_whitespace():
        """JSON with extra whitespace."""
        response = '```json  \n{"key": "value"}  \n```'
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result == {"key": "value"}

    @staticmethod
    def test_missing_json_marker():
        """Missing ```json marker returns None."""
        response = '{"result": true}'
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_invalid_json_content():
        """Invalid JSON content returns None."""
        response = "```json\nnot valid json\n```"
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_empty_json_block():
        """Empty JSON block returns None."""
        response = "```json\n```"
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_json_decode_error():
        """JSON decode error returns None."""
        response = '```json\n{"incomplete": json}\n```'
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_nested_json():
        """Nested JSON structure."""
        response = '```json\n{"outer": {"inner": [1, 2, 3]}}\n```'
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result["outer"]["inner"] == [1, 2, 3]

    @staticmethod
    def test_json_with_special_chars():
        """JSON with special characters."""
        response = '```json\n{"text": "Hello\\nWorld"}\n```'
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result["text"] == "Hello\nWorld"

    @staticmethod
    def test_json_null_value():
        """JSON with null value."""
        response = '```json\n{"key": null}\n```'
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result["key"] is None

    @staticmethod
    def test_json_array():
        """JSON with array at root."""
        response = "```json\n[1, 2, 3]\n```"
        result = TuneUtils.parse_json_from_llm_response(response)
        assert result == [1, 2, 3]


class TestParseListFromLlMResponse:
    """Test parse_list_from_llm_response function."""

    @staticmethod
    def test_valid_list_block():
        """Valid list in code block."""
        response = "```list\n[1, 2, 3]\n```"
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result == [1, 2, 3]

    @staticmethod
    def test_list_with_whitespace():
        """List with extra whitespace."""
        response = "```list  \n[1, 2, 3]  \n```"
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result == [1, 2, 3]

    @staticmethod
    def test_missing_list_marker():
        """Missing ```list marker returns None."""
        response = "[1, 2, 3]"
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_invalid_list_content():
        """Invalid list content returns None."""
        response = "```list\nnot a list\n```"
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_empty_list_block():
        """Empty list block returns None."""
        response = "```list\n```"
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_dict_not_list():
        """Dict is not a list, returns None."""
        response = '```list\n{"key": "value"}\n```'
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_nested_list():
        """Nested list structure."""
        response = "```list\n[[1, 2], [3, 4]]\n```"
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result == [[1, 2], [3, 4]]

    @staticmethod
    def test_list_with_mixed_types():
        """List with mixed types."""
        response = '```list\n[1, "two", 3.0, true, null]\n```'
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result == [1, "two", 3.0, True, None]

    @staticmethod
    def test_list_with_nested_dict():
        """List with nested dict."""
        response = '```list\n[{"a": 1}, {"b": 2}]\n```'
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result == [{"a": 1}, {"b": 2}]

    @staticmethod
    def test_string_not_list():
        """String literal is not a list, returns None."""
        response = '```list\n"not a list"\n```'
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result is None

    @staticmethod
    def test_number_not_list():
        """Number literal is not a list, returns None."""
        response = "```list\n42\n```"
        result = TuneUtils.parse_list_from_llm_response(response)
        assert result is None


class TestConvertCasesToExamples:
    """Test convert_cases_to_examples function."""

    @staticmethod
    def test_empty_cases():
        """Empty list returns empty string."""
        result = TuneUtils.convert_cases_to_examples([])
        assert result == ""

    @staticmethod
    def test_single_case_format():
        """Single case formatted correctly."""
        case = Case(inputs={"query": "hello"}, label={"answer": "world"})
        result = TuneUtils.convert_cases_to_examples([case])
        assert "example 1:" in result
        assert "[question]:" in result
        assert "[expected answer]:" in result
        assert "hello" in result
        assert "world" in result

    @staticmethod
    def test_multiple_cases():
        """Multiple cases formatted correctly."""
        cases = [
            Case(inputs={"q": "a"}, label={"ans": "1"}),
            Case(inputs={"q": "b"}, label={"ans": "2"}),
        ]
        result = TuneUtils.convert_cases_to_examples(cases)
        assert "example 1:" in result
        assert "example 2:" in result
        assert "[question]:" in result
        assert "[expected answer]:" in result

    @staticmethod
    def test_complex_inputs():
        """Complex input/output dict."""
        case = Case(inputs={"query": "test", "context": "info"}, label={"answer": "result", "confidence": 0.9})
        result = TuneUtils.convert_cases_to_examples([case])
        assert "query:test" in result or "query: test" in result

    @staticmethod
    def test_evaluated_case():
        """Works with EvaluatedCase."""
        case = Case(inputs={"q": "test"}, label={"a": "ans"})
        eval_case = EvaluatedCase(case=case, score=0.8)
        result = TuneUtils.convert_cases_to_examples([eval_case])
        assert "example 1:" in result
        assert "[question]:" in result


class TestGetInputStringFromCase:
    """Test get_input_string_from_case function."""

    @staticmethod
    def test_case_with_inputs_dict():
        """Case with dict inputs."""
        case = Case(inputs={"query": "hello"}, label={"answer": "world"})
        result = TuneUtils.get_input_string_from_case(case)
        assert "query:hello" in result or "query: hello" in result

    @staticmethod
    def test_case_with_empty_inputs():
        """Case with minimal inputs."""
        case = Case(inputs={"q": "?"}, label={"a": "!"})
        result = TuneUtils.get_input_string_from_case(case)
        assert "q:?" in result or "q: ?" in result


class TestGetOutputStringFromMessage:
    """Test get_output_string_from_message function."""

    @staticmethod
    def test_assistant_message_without_tool_calls():
        """Assistant message without tool calls."""
        msg = AssistantMessage(content="Hello, world!")
        result = TuneUtils.get_output_string_from_message(msg)
        assert result == "Hello, world!"

    @staticmethod
    def test_assistant_message_with_tool_calls():
        """Assistant message with tool calls."""
        tc = create_tool_call("test_func", '{"arg": "val"}')
        msg = AssistantMessage(content="", tool_calls=[tc])
        result = TuneUtils.get_output_string_from_message(msg)
        assert "test_func" in result

    @staticmethod
    def test_user_message():
        """User message returns content."""
        msg = UserMessage(content="Test message")
        result = TuneUtils.get_output_string_from_message(msg)
        assert result == "Test message"

    @staticmethod
    def test_system_message():
        """System message returns content."""
        msg = SystemMessage(content="You are a helpful assistant.")
        result = TuneUtils.get_output_string_from_message(msg)
        assert result == "You are a helpful assistant."

    @staticmethod
    def test_message_with_empty_content():
        """Message with empty content."""
        msg = AssistantMessage(content="")
        result = TuneUtils.get_output_string_from_message(msg)
        assert result == ""

    @staticmethod
    def test_multiple_tool_calls():
        """Message with multiple tool calls."""
        tc1 = create_tool_call("func1", "{}", "call_1")
        tc2 = create_tool_call("func2", "{}", "call_2")
        msg = AssistantMessage(content="", tool_calls=[tc1, tc2])
        result = TuneUtils.get_output_string_from_message(msg)
        assert "func1" in result
        assert "func2" in result


class TestGetContentStringFromTemplate:
    """Test get_content_string_from_template function."""

    @staticmethod
    def test_template_with_messages():
        """Template with messages returns content."""
        template = PromptTemplate(
            content=[
                SystemMessage(content="You are a helpful assistant."),
                UserMessage(content="Hello!"),
            ]
        )
        result = TuneUtils.get_content_string_from_template(template)
        assert "You are a helpful assistant" in result or "helpful" in result
        assert "Hello!" in result

    @staticmethod
    def test_single_message_template():
        """Template with single message."""
        template = PromptTemplate(content=[SystemMessage(content="Only one.")])
        result = TuneUtils.get_content_string_from_template(template)
        assert result == "Only one."

    @staticmethod
    def test_empty_template():
        """Empty template returns empty string."""
        template = PromptTemplate(content=[])
        result = TuneUtils.get_content_string_from_template(template)
        assert result == ""

    @staticmethod
    def test_template_with_special_chars():
        """Template with special characters."""
        template = PromptTemplate(
            content=[
                UserMessage(content="Line1\nLine2\tTab"),
            ]
        )
        result = TuneUtils.get_content_string_from_template(template)
        assert "Line1" in result
