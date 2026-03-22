# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import tempfile

import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.dev_tools.agent_builder.utils.utils import (
    deep_merge_dict,
    extract_json_from_text,
    format_dialog_history,
    merge_dict_lists,
    safe_json_loads,
    validate_session_id,
)


class TestExtractJsonFromText:
    @staticmethod
    def test_extract_from_json_code_block():
        text = '```json\n{"key": "value"}\n```'
        result = extract_json_from_text(text)
        assert result == '{"key": "value"}'

    @staticmethod
    def test_extract_from_plain_code_block():
        text = '```\n{"key": "value"}\n```'
        result = extract_json_from_text(text)
        assert result == '{"key": "value"}'

    @staticmethod
    def test_extract_from_text_without_code_block():
        text = '{"key": "value"}'
        result = extract_json_from_text(text)
        assert result == '{"key": "value"}'

    @staticmethod
    def test_extract_from_empty_text():
        result = extract_json_from_text("")
        assert result == ""

    @staticmethod
    def test_extract_from_none_text():
        result = extract_json_from_text(None)
        assert result is None

    @staticmethod
    def test_extract_json_array():
        text = '```json\n[1, 2, 3]\n```'
        result = extract_json_from_text(text)
        assert result == '[1, 2, 3]'

    @staticmethod
    def test_extract_multiline_json():
        text = '```json\n{"key1": "value1",\n"key2": "value2"}\n```'
        result = extract_json_from_text(text)
        assert "key1" in result
        assert "key2" in result


class TestFormatDialogHistory:
    @staticmethod
    def test_format_single_message():
        history = [{"role": "user", "content": "Hello"}]
        result = format_dialog_history(history)
        assert result == "user: Hello"

    @staticmethod
    def test_format_multiple_messages():
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]
        result = format_dialog_history(history)
        assert result == "user: Hello\nassistant: Hi there!"

    @staticmethod
    def test_format_empty_history():
        result = format_dialog_history([])
        assert result == ""

    @staticmethod
    def test_format_with_custom_separator():
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"}
        ]
        result = format_dialog_history(history, separator=" | ")
        assert result == "user: Hello | assistant: Hi!"

    @staticmethod
    def test_format_with_missing_keys():
        history = [
            {"role": "user"},
            {"content": "Missing role"}
        ]
        result = format_dialog_history(history)
        assert "user: " in result
        assert "unknown: Missing role" in result


class TestSafeJsonLoads:
    @staticmethod
    def test_loads_valid_json():
        result = safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    @staticmethod
    def test_loads_invalid_json_returns_default():
        result = safe_json_loads('invalid json', default={})
        assert result == {}

    @staticmethod
    def test_loads_empty_string_returns_default():
        result = safe_json_loads('', default=None)
        assert result is None

    @staticmethod
    def test_loads_none_returns_default():
        result = safe_json_loads(None, default=[])
        assert result == []

    @staticmethod
    def test_loads_json_array():
        result = safe_json_loads('[1, 2, 3]')
        assert result == [1, 2, 3]


class TestValidateSessionId:
    @staticmethod
    def test_valid_session_id_with_alphanumeric():
        assert validate_session_id("session123") is True

    @staticmethod
    def test_valid_session_id_with_underscore():
        assert validate_session_id("session_123") is True

    @staticmethod
    def test_valid_session_id_with_hyphen():
        assert validate_session_id("session-123") is True

    @staticmethod
    def test_valid_session_id_combined():
        assert validate_session_id("session_123-abc") is True

    @staticmethod
    def test_invalid_session_id_with_special_chars():
        assert validate_session_id("session@123") is False

    @staticmethod
    def test_invalid_session_id_with_space():
        assert validate_session_id("session 123") is False

    @staticmethod
    def test_invalid_empty_session_id():
        assert validate_session_id("") is False

    @staticmethod
    def test_invalid_none_session_id():
        assert validate_session_id(None) is False


class TestMergeDictLists:
    @staticmethod
    def test_merge_with_unique_keys():
        existing = [{"id": "1", "name": "A"}]
        new_items = [{"id": "2", "name": "B"}]
        result = merge_dict_lists(existing, new_items, "id")
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1]["id"] == "2"

    @staticmethod
    def test_merge_with_duplicate_keys():
        existing = [{"id": "1", "name": "A"}]
        new_items = [{"id": "1", "name": "B"}]
        result = merge_dict_lists(existing, new_items, "id")
        assert len(result) == 1
        assert result[0]["name"] == "A"

    @staticmethod
    def test_merge_empty_new_items():
        existing = [{"id": "1", "name": "A"}]
        result = merge_dict_lists(existing, [], "id")
        assert len(result) == 1

    @staticmethod
    def test_merge_empty_existing():
        new_items = [{"id": "1", "name": "A"}]
        result = merge_dict_lists([], new_items, "id")
        assert len(result) == 1

    @staticmethod
    def test_merge_both_empty():
        result = merge_dict_lists([], [], "id")
        assert result == []

    @staticmethod
    def test_merge_with_missing_unique_key():
        existing = [{"id": "1", "name": "A"}]
        new_items = [{"name": "B"}]
        result = merge_dict_lists(existing, new_items, "id")
        assert len(result) == 1


class TestDeepMergeDict:
    @staticmethod
    def test_merge_simple_dicts():
        base = {"a": 1}
        update = {"b": 2}
        result = deep_merge_dict(base, update)
        assert result == {"a": 1, "b": 2}

    @staticmethod
    def test_merge_nested_dicts():
        base = {"a": {"b": 1, "c": 2}}
        update = {"a": {"b": 3, "d": 4}}
        result = deep_merge_dict(base, update)
        assert result == {"a": {"b": 3, "c": 2, "d": 4}}

    @staticmethod
    def test_merge_overwrites_non_dict_values():
        base = {"a": 1}
        update = {"a": 2}
        result = deep_merge_dict(base, update)
        assert result == {"a": 2}

    @staticmethod
    def test_merge_does_not_modify_original():
        base = {"a": 1}
        update = {"b": 2}
        result = deep_merge_dict(base, update)
        assert "b" not in base
        assert "b" in result

    @staticmethod
    def test_merge_empty_dicts():
        result = deep_merge_dict({}, {})
        assert result == {}
