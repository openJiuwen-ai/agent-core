# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.foundation.llm.schema.message_chunk import (
    AssistantMessageChunk,
    ToolMessageChunk,
    merge_parser_content,
    merge_dicts,
    merge_pydantic_models,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


# Tests for merge_parser_content
def test_merge_parser_content_both_none():
    """Test merge_parser_content when both values are None."""
    assert merge_parser_content(None, None) is None


def test_merge_parser_content_left_none():
    """Test merge_parser_content when left is None."""
    result = merge_parser_content(None, "right")
    assert result == "right"


def test_merge_parser_content_right_none():
    """Test merge_parser_content when right is None."""
    result = merge_parser_content("left", None)
    assert result == "left"


def test_merge_parser_content_strings():
    """Test merge_parser_content concatenates strings."""
    result = merge_parser_content("left", "right")
    assert result == "leftright"


def test_merge_parser_content_lists():
    """Test merge_parser_content concatenates lists."""
    result = merge_parser_content([1, 2], [3, 4])
    assert result == [1, 2, 3, 4]


def test_merge_parser_content_dicts():
    """Test merge_parser_content merges dicts recursively."""
    result = merge_parser_content({"a": "1"}, {"b": "2"})
    assert result == {"a": "1", "b": "2"}


def test_merge_parser_content_dict_string_concat():
    """Test merge_parser_content merges same key in dicts as strings."""
    result = merge_parser_content({"key": "left"}, {"key": "right"})
    assert result == {"key": "leftright"}


# Tests for merge_dicts
def test_merge_dicts_empty():
    """Test merge_dicts with empty dict."""
    result = merge_dicts({}, {"a": 1})
    assert result == {"a": 1}


def test_merge_dicts_simple():
    """Test merge_dicts with simple dicts."""
    result = merge_dicts({"a": 1}, {"b": 2})
    assert result == {"a": 1, "b": 2}


def test_merge_dicts_string_concat():
    """Test merge_dicts concatenates string values."""
    result = merge_dicts({"key": "left"}, {"key": "right"})
    assert result == {"key": "leftright"}


def test_merge_dicts_list_concat():
    """Test merge_dicts concatenates list values."""
    result = merge_dicts({"key": [1, 2]}, {"key": [3, 4]})
    assert result == {"key": [1, 2, 3, 4]}


def test_merge_dicts_nested():
    """Test merge_dicts with nested dicts."""
    result = merge_dicts(
        {"outer": {"inner": "left"}},
        {"outer": {"inner": "right"}}
    )
    assert result == {"outer": {"inner": "leftright"}}


def test_merge_dicts_overwrite():
    """Test merge_dicts overwrites different types."""
    result = merge_dicts({"key": "string"}, {"key": 123})
    assert result == {"key": 123}


# Tests for merge_pydantic_models
def test_merge_pydantic_models_tool_call():
    """Test merge_pydantic_models with ToolCall."""
    left = ToolCall(id="call_1", type="function", name="func", arguments="{", index=0)
    right = ToolCall(
        id="call_1", type="function", name="", arguments='"x": 1}'
    )
    result = merge_pydantic_models(left, right)
    # merge_pydantic_models concatenates same-type fields
    assert result.id == "call_1call_1"
    assert result.name == "func"
    assert result.arguments == '{"x": 1}'
    assert result.index == 0


def test_merge_pydantic_models_different_types():
    """Test merge_pydantic_models with different types returns right."""
    left = ToolCall(id="call_1", type="function", name="func", arguments="{}", index=0)
    right = {"not": "a_tool_call"}
    result = merge_pydantic_models(left, right)
    assert result == right


# Tests for AssistantMessageChunk.__add__
def test_assistant_add_merges_content_strings():
    """Test that __add__ merges string content."""
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="Hello ",
        tool_calls=None,
    )
    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="world!",
        tool_calls=None,
    )

    result = chunk1 + chunk2
    assert result.content == "Hello world!"


def test_assistant_add_merges_content_lists():
    """Test that __add__ merges list content."""
    content1 = {"type": "text", "text": "Hello"}
    content2 = {"type": "text", "text": "world"}
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content=[content1],
        tool_calls=None,
    )
    chunk2 = AssistantMessageChunk(
        role="assistant",
        content=[content2],
        tool_calls=None,
    )

    result = chunk1 + chunk2
    assert result.content == [content1, content2]


def test_assistant_add_merges_tool_calls_with_same_id():
    """Test that __add__ merges tool calls with same ID by concatenating."""
    tc1 = ToolCall(id="call_1", type="function", name="func", arguments="{", index=0)
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=[tc1],
    )

    tc2 = ToolCall(
        id="call_1", type="function", name="", arguments='"x": 1}'
    )
    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=[tc2],
    )

    result = chunk1 + chunk2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "func"
    assert result.tool_calls[0].arguments == '{"x": 1}'
    assert result.tool_calls[0].index == 0


def test_assistant_add_appends_different_tool_calls():
    """Test that __add__ appends different tool calls."""
    tc1 = ToolCall(id="call_1", type="function", name="func1", arguments="{}", index=0)
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=[tc1],
    )

    tc2 = ToolCall(id="call_2", type="function", name="func2", arguments="{}", index=1)
    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=[tc2],
    )

    result = chunk1 + chunk2
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[1].id == "call_2"


def test_assistant_add_merges_tool_calls_without_id():
    """Test that __add__ merges tool calls without ID."""
    tc1 = ToolCall(id=None, type="function", name="func", arguments="{", index=0)
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=[tc1],
    )

    tc2 = ToolCall(id=None, type="function", name="", arguments='"x": 1}')
    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=[tc2],
    )

    result = chunk1 + chunk2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "func"
    assert result.tool_calls[0].arguments == '{"x": 1}'
    assert result.tool_calls[0].index == 0


def test_assistant_add_copies_tool_call_with_index():
    """Test that __add__ copies tool calls preserving index field."""
    tc = ToolCall(id="call_1", type="function", name="func", arguments="{}", index=5)
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=[tc],
    )

    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
    )

    result = chunk1 + chunk2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].index == 5


def test_assistant_add_merges_reasoning_content():
    """Test that __add__ concatenates reasoning_content."""
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        reasoning_content="Thinking step 1",
    )

    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        reasoning_content="Thinking step 2",
    )

    result = chunk1 + chunk2
    assert result.reasoning_content == "Thinking step 1Thinking step 2"


def test_assistant_add_handles_none_reasoning_content():
    """Test that __add__ handles None reasoning_content."""
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        reasoning_content=None,
    )

    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        reasoning_content="Some reasoning",
    )

    result = chunk1 + chunk2
    assert result.reasoning_content == "Some reasoning"


def test_assistant_add_merges_finish_reason():
    """Test that __add__ handles finish_reason."""
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        finish_reason="null",
    )

    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        finish_reason="stop",
    )

    result = chunk1 + chunk2
    assert result.finish_reason == "stop"


def test_assistant_add_ignores_null_finish_reason():
    """Test that __add__ ignores 'null' finish_reason."""
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        finish_reason="stop",
    )

    chunk2 = AssistantMessageChunk(
        role="assistant",
        content="",
        tool_calls=None,
        finish_reason="null",
    )

    result = chunk1 + chunk2
    assert result.finish_reason == "stop"


def test_assistant_add_raises_type_error_for_mismatched_types():
    """Test that __add__ raises TypeError for mismatched types."""
    chunk1 = AssistantMessageChunk(
        role="assistant",
        content="Hello",
        tool_calls=None,
    )

    with pytest.raises(TypeError):
        chunk1 + "not a chunk"


# Tests for ToolMessageChunk.__add__
def test_tool_add_merges_content():
    """Test that __add__ merges content."""
    chunk1 = ToolMessageChunk(
        role="tool",
        content="Partial ",
        tool_call_id="call_123",
    )

    chunk2 = ToolMessageChunk(
        role="tool",
        content="result",
        tool_call_id="call_123",
    )

    result = chunk1 + chunk2
    assert result.content == "Partial result"


def test_tool_add_preserves_tool_call_id():
    """Test that __add__ preserves tool_call_id."""
    chunk1 = ToolMessageChunk(
        role="tool",
        content="",
        tool_call_id="call_123",
    )

    chunk2 = ToolMessageChunk(
        role="tool",
        content="result",
        tool_call_id="",
    )

    result = chunk1 + chunk2
    assert result.tool_call_id == "call_123"


def test_tool_add_handles_none_content():
    """Test that __add__ handles empty content."""
    chunk1 = ToolMessageChunk(
        role="tool",
        content="",
        tool_call_id="call_123",
    )

    chunk2 = ToolMessageChunk(
        role="tool",
        content="result",
        tool_call_id="call_123",
    )

    result = chunk1 + chunk2
    assert result.content == "result"
