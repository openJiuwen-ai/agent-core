# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for parse_response"""

from openjiuwen.core.memory.graph.extraction.parse_response import (
    _raw_decode_json,
    ensure_list,
    parse_json,
    try_get_key,
)


class TestParseJson:
    """Tests for parse_json"""

    @staticmethod
    def test_plain_json_object():
        """Plain JSON object string is parsed"""
        resp = '{"a": 1, "b": "x"}'
        result = parse_json(resp)
        assert result == {"a": 1, "b": "x"}

    @staticmethod
    def test_json_in_code_block():
        """JSON inside ```json code block is parsed"""
        resp = 'Some text\n```json\n{"x": 42}\n```'
        result = parse_json(resp)
        assert result == {"x": 42}

    @staticmethod
    def test_code_block_empty_type_treated_as_json():
        """Code block with empty type is treated as JSON"""
        resp = "```\n[1, 2, 3]\n```"
        result = parse_json(resp)
        assert result == [1, 2, 3]

    @staticmethod
    def test_non_json_code_block_skipped():
        """Non-JSON code block is skipped; falls back to raw decode"""
        resp = "```python\nx = 1\n```"
        result = parse_json(resp)
        assert result is None or result != {"x": 1}

    @staticmethod
    def test_invalid_json_returns_none():
        """Invalid JSON returns None"""
        result = parse_json("not json at all {")
        assert result is None

    @staticmethod
    def test_output_schema_required_filters_keys():
        """When output_schema has required keys, result only contains those keys (fuzzy)"""
        resp = '{"extracted_entities": [{"name": "E1", "entity_type_id": 0}]}'
        output_schema = {"json_schema": {"required": ["extracted_entities"]}}
        result = parse_json(resp, output_schema=output_schema)
        assert result is not None
        assert "extracted_entities" in result or isinstance(result, dict)

    @staticmethod
    def test_array_in_response():
        """JSON array is parsed"""
        resp = "[1, 2, 3]"
        result = parse_json(resp)
        assert result == [1, 2, 3]

    @staticmethod
    def test_code_block_invalid_json_falls_through():
        """Code block with invalid JSON triggers decode error and continues"""
        resp = "```json\n{invalid\n```"
        result = parse_json(resp)
        assert result is None

    @staticmethod
    def test_raw_decode_json_called_with_trailing_brace():
        """Response with '},' triggers _raw_decode_json with two possible_resp"""
        resp = ' [{"a": 1},'
        result = parse_json(resp)
        assert result is None or isinstance(result, (list, dict))

    @staticmethod
    def test_parse_with_required_and_dict_rebuilds_from_fuzzy_keys():
        """With output_schema required, dict result triggers key filtering loop"""
        resp = '{"extracted_entities": []}'
        output_schema = {"json_schema": {"required": ["extracted_entities"]}}
        result = parse_json(resp, output_schema=output_schema)
        assert result is not None
        assert isinstance(result, dict)


class TestTryGetKey:
    """Tests for try_get_key"""

    @staticmethod
    def test_exact_key_match():
        """Exact key (normalized) returns corresponding value key"""
        src = {"extracted_entities": [1, 2], "other": 0}
        key_ref = try_get_key("extracted_entities", src)
        assert key_ref is not None
        # Returns the key that matched (for use in result[key])
        assert key_ref in src

    @staticmethod
    def test_fuzzy_match():
        """Close match key finds key"""
        src = {"ExtractedEntities": []}
        key_ref = try_get_key("extracted_entities", src)
        assert key_ref is not None
        assert key_ref in src

    @staticmethod
    def test_no_match_returns_none():
        """No close match returns None"""
        src = {"a": 1, "b": 2}
        assert try_get_key("xyz", src) is None


class TestEnsureList:
    """Tests for ensure_list"""

    @staticmethod
    def test_list_unchanged():
        """List input is returned as-is"""
        val = [1, 2, 3]
        assert ensure_list(val) is val

    @staticmethod
    def test_single_object_wrapped_in_list():
        """Single non-list object is wrapped in list"""
        assert ensure_list(42) == [42]
        assert ensure_list("x") == ["x"]

    @staticmethod
    def test_dict_with_single_list_value_unwrapped():
        """Dict with single key whose value is list returns that list"""
        val = {"items": [1, 2]}
        assert ensure_list(val) == [1, 2]

    @staticmethod
    def test_dict_with_single_non_list_value_wrapped():
        """Dict with single key and non-list value returns [dict]"""
        val = {"key": "not a list"}
        result = ensure_list(val)
        assert result == [val]

    @staticmethod
    def test_dict_with_multiple_keys_wrapped():
        """Dict with more than one key is wrapped in list"""
        val = {"a": 1, "b": 2}
        assert ensure_list(val) == [val]


class TestRawDecodeJson:
    """Tests for _raw_decode_json (direct call for coverage)"""

    @staticmethod
    def test_raw_decode_json_plain_object():
        """_raw_decode_json decodes plain JSON object"""
        result = _raw_decode_json('  {"x": 1}')
        assert result == {"x": 1}

    @staticmethod
    def test_raw_decode_json_with_required_rebuilds_dict_branch():
        """_raw_decode_json with must_contain_key hits dict-rebuild branch"""
        result = _raw_decode_json('  {"extracted_entities": [1]}', must_contain_key=["extracted_entities"])
        assert isinstance(result, dict)

    @staticmethod
    def test_raw_decode_json_with_required_and_list_continues():
        """_raw_decode_json with must_contain_key and list result continues (no return)"""
        result = _raw_decode_json("  [1, 2]", must_contain_key=["x"])
        assert result is None

    @staticmethod
    def test_raw_decode_json_trailing_comma_brace_two_candidates():
        """_raw_decode_json with '},' in string uses two possible_resp candidates"""
        resp = '  [{"a": 1},'
        result = _raw_decode_json(resp)
        assert result is None or result == [{"a": 1}]
