# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for shared IgnoredToolCallRule and matches_ignored_tool_call."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openjiuwen.core.common.schema.ignored_tool_call import (
    WRAPPER_TOOL_NAMES,
    IgnoredToolCallRule,
    matches_ignored_tool_call,
)


class TestIgnoredToolCallRule:
    def test_forbid_unknown_fields(self):
        with pytest.raises(ValidationError):
            IgnoredToolCallRule(
                tool_name="image_get_run",
                argument_equals={},
                unexpected=True,
            )

    def test_wrapper_empty_equals_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IgnoredToolCallRule(tool_name="invoke_tool", argument_equals={})
        err = str(exc_info.value).lower()
        assert "invoke_tool" in err or "wrapper" in err or "argument_equals" in err

    def test_non_wrapper_empty_equals_allowed(self):
        rule = IgnoredToolCallRule(tool_name="image_get_run", argument_equals={})
        assert rule.tool_name == "image_get_run"
        assert rule.argument_equals == {}

    def test_wrapper_name_constant(self):
        assert "invoke_tool" in WRAPPER_TOOL_NAMES


class TestMatchesIgnoredToolCall:
    def test_missing_key_with_none_rule_value_does_not_match(self):
        rules = [
            IgnoredToolCallRule(tool_name="poll", argument_equals={"k": None}),
        ]
        assert matches_ignored_tool_call("poll", {}, rules) is False

    def test_value_match_true(self):
        rules = [
            IgnoredToolCallRule(
                tool_name="invoke_tool",
                argument_equals={"tool_name": "image_get_run"},
            ),
        ]
        assert (
            matches_ignored_tool_call(
                "invoke_tool",
                {"tool_name": "image_get_run", "arguments": {}},
                rules,
            )
            is True
        )

    def test_missing_key_false(self):
        rules = [
            IgnoredToolCallRule(
                tool_name="invoke_tool",
                argument_equals={"tool_name": "image_get_run"},
            ),
        ]
        assert matches_ignored_tool_call("invoke_tool", {"arguments": {}}, rules) is False

    def test_value_mismatch_false(self):
        rules = [
            IgnoredToolCallRule(
                tool_name="invoke_tool",
                argument_equals={"tool_name": "image_get_run"},
            ),
        ]
        assert (
            matches_ignored_tool_call(
                "invoke_tool",
                {"tool_name": "image_create_run"},
                rules,
            )
            is False
        )

    def test_empty_equals_matches_by_name_only(self):
        rules = [
            IgnoredToolCallRule(tool_name="image_get_run", argument_equals={}),
        ]
        assert matches_ignored_tool_call("image_get_run", {}, rules) is True
        assert matches_ignored_tool_call("image_get_run", {"any": 1}, rules) is True
        assert matches_ignored_tool_call("other", {}, rules) is False
