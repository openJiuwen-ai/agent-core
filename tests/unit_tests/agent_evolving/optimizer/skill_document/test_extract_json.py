# coding: utf-8
"""Tests for _extract_json and _extract_json_with_error helper functions."""

import json

from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    _extract_json,
    _extract_json_with_error,
)


class TestExtractJson:
    @staticmethod
    def test_valid_json_object():
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    @staticmethod
    def test_valid_json_array():
        result = _extract_json('[1, 2, 3]')
        assert result == [1, 2, 3]

    @staticmethod
    def test_empty_string_returns_none():
        result = _extract_json("")
        assert result is None

    @staticmethod
    def test_whitespace_only_returns_none():
        result = _extract_json("   \n  ")
        assert result is None

    @staticmethod
    def test_json_in_markdown_code_block():
        raw = '```json\n{"edits": []}\n```'
        result = _extract_json(raw)
        assert result == {"edits": []}

    @staticmethod
    def test_json_embedded_in_text():
        raw = 'Here is the result: {"edits": [{"op": "append"}]} done.'
        result = _extract_json(raw)
        assert result is not None
        assert result["edits"][0]["op"] == "append"

    @staticmethod
    def test_json_array_embedded_in_text():
        raw = "The list is [1, 2, 3] as shown."
        result = _extract_json(raw)
        assert result == [1, 2, 3]

    @staticmethod
    def test_completely_invalid_text_returns_none():
        result = _extract_json("hello world no json here")
        assert result is None

    @staticmethod
    def test_trailing_comma_fix():
        raw = '{"edits": [{"op": "append"},]}'
        result = _extract_json(raw)
        assert result is not None
        assert len(result["edits"]) == 1

    @staticmethod
    def test_nested_json_object():
        raw = '{"patch": {"edits": [{"op": "append", "content": "x"}]}, "reasoning": "test"}'
        result = _extract_json(raw)
        assert result["patch"]["edits"][0]["op"] == "append"

    @staticmethod
    def test_json_with_leading_whitespace():
        result = _extract_json('  \n  {"key": "val"}  ')
        assert result == {"key": "val"}

    @staticmethod
    def test_broken_json_in_braces_fallback():
        """Regex fallback extracts and fixes JSON from within text."""
        raw = 'prefix {"key": "val",} suffix'
        result = _extract_json(raw)
        assert result is not None
        assert result["key"] == "val"


class TestExtractJsonWithError:
    @staticmethod
    def test_valid_json_no_error():
        result, error = _extract_json_with_error('{"ok": true}')
        assert result == {"ok": True}
        assert error == ""

    @staticmethod
    def test_empty_string_returns_error():
        result, error = _extract_json_with_error("")
        assert result is None
        assert "empty" in error.lower() or error != ""

    @staticmethod
    def test_invalid_json_returns_error_message():
        result, error = _extract_json_with_error("{broken")
        assert result is None
        assert len(error) > 0
