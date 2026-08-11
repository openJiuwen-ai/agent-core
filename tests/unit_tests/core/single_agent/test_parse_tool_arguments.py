# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for AbilityManager._parse_tool_arguments / _repair_bare_text_values.

BUG2026080701339: LLM returns bare (unquoted) text values in tool-call
arguments, e.g. ``{"tasks": scan all pages}`` instead of
``{"tasks": "scan all pages"}``.  The old code silently returned ``{}``,
which caused consecutive tool-call failures and eventually triggered the
circuit breaker after 5 failures.

The fix adds:
1. ``_repair_bare_text_values`` – regex-based repair that quotes bare
   text values while preserving already-quoted strings and JSON literals.
2. ``_parse_tool_arguments_with_repair`` – returns the repaired JSON
   string alongside the parsed dict so callers can inspect the repair.
3. ``_parse_tool_arguments`` now raises ``ValueError`` instead of
   silently returning ``{}`` when all repair attempts fail, so the LLM
   receives a diagnostic message and can self-correct.
"""

from __future__ import annotations

import json
import unittest

import pytest

from openjiuwen.core.single_agent.ability_manager import AbilityManager


class TestRepairBareTextValues(unittest.TestCase):
    """Tests for AbilityManager._repair_bare_text_values."""

    def test_bare_text_value_is_quoted(self):
        result = AbilityManager._repair_bare_text_values(
            '{"tasks": scan all pages}'
        )
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["tasks"], "scan all pages")

    def test_bare_chinese_text_value_is_quoted(self):
        raw = '{"tasks": 扫描模板PPT色彩方案;读取两份PPT的HTML页面结构和CSS变量}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertIn("扫描模板PPT色彩方案", parsed["tasks"])

    def test_bare_value_from_real_bug_log(self):
        raw = (
            '{"tasks": 扫描page-1到page-8，提取标题、百分比、'
            '图表、数据来源、事故案例等信息;'
            '扫描page-9到page-16，提取同样信息;'
            '汇总所有页面信息清单}'
        )
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertIn("扫描page-1", parsed["tasks"])

    def test_multiple_bare_values(self):
        raw = '{"name": hello, "desc": world}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["name"], "hello")
        self.assertEqual(parsed["desc"], "world")

    def test_already_quoted_values_unchanged(self):
        raw = '{"tasks": "scan all pages"}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNone(result)

    def test_json_literals_not_quoted(self):
        raw = '{"active": true, "count": 42, "label": null}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        self.assertIsNone(AbilityManager._repair_bare_text_values(""))
        self.assertIsNone(AbilityManager._repair_bare_text_values("   "))

    def test_mixed_quoted_and_bare(self):
        raw = '{"tasks": "read file", "action": update}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["tasks"], "read file")
        self.assertEqual(parsed["action"], "update")

    def test_bare_value_with_trailing_spaces(self):
        raw = '{"tasks": scan all pages  }'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["tasks"], "scan all pages")

    def test_bare_value_containing_backslash(self):
        raw = '{"path": C:\\\\Users\\\\docs}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertIn("C:", parsed["path"])

    def test_bare_value_containing_double_quote_not_repairable_by_regex(self):
        raw = '{"msg": say "hello"}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNone(result)

    def test_numeric_values_not_quoted(self):
        raw = '{"count": 42, "ratio": 3.14}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNone(result)

    def test_negative_number_not_quoted(self):
        raw = '{"offset": -1}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNone(result)

    def test_nested_object_with_bare_value(self):
        raw = '{"config": {"mode": auto}}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["config"]["mode"], "auto")

    def test_bare_value_with_semicolons(self):
        raw = '{"tasks": 设计用户界面;实现接口集成;添加单元测试}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertIn("设计用户界面", parsed["tasks"])

    def test_bare_value_with_parentheses(self):
        raw = '{"desc": fix layout (v2)}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertIn("fix layout", parsed["desc"])

    def test_dot_prefixed_float_not_quoted(self):
        raw = '{"ratio": .5}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNone(result)

    def test_mixed_bare_and_literal(self):
        raw = '{"active": true, "tasks": scan all pages}'
        result = AbilityManager._repair_bare_text_values(raw)
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertTrue(parsed["active"])
        self.assertEqual(parsed["tasks"], "scan all pages")


class TestRepairToolArgumentsJson(unittest.TestCase):
    """Tests for AbilityManager._repair_tool_arguments_json (bracket balancing)."""

    def test_missing_closing_brace(self):
        raw = '{"tasks": "scan all pages"'
        result = AbilityManager._repair_tool_arguments_json(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result, '{"tasks": "scan all pages"}')

    def test_missing_closing_bracket(self):
        raw = '{"items": [1, 2, 3'
        result = AbilityManager._repair_tool_arguments_json(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result, '{"items": [1, 2, 3]}')

    def test_empty_string_returns_none(self):
        self.assertIsNone(AbilityManager._repair_tool_arguments_json(""))
        self.assertIsNone(AbilityManager._repair_tool_arguments_json("   "))

    def test_already_valid_json_returns_same(self):
        raw = '{"tasks": "hello"}'
        result = AbilityManager._repair_tool_arguments_json(raw)
        self.assertEqual(result, raw)

    def test_mismatched_bracket_returns_none(self):
        raw = '{"tasks": "hello"}]'
        result = AbilityManager._repair_tool_arguments_json(raw)
        self.assertIsNone(result)


class TestParseToolArguments(unittest.TestCase):
    """Tests for AbilityManager._parse_tool_arguments."""

    def test_valid_json_passes_through(self):
        raw = '{"tasks": "scan all pages"}'
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertEqual(result, {"tasks": "scan all pages"})

    def test_non_string_input_returned_as_is(self):
        self.assertIsNone(AbilityManager._parse_tool_arguments(None))
        self.assertEqual(AbilityManager._parse_tool_arguments(42), 42)
        self.assertEqual(
            AbilityManager._parse_tool_arguments({"a": 1}),
            {"a": 1},
        )

    def test_bare_text_value_repaired(self):
        raw = '{"tasks": scan all pages}'
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertEqual(result, {"tasks": "scan all pages"})

    def test_missing_closing_brace_repaired(self):
        raw = '{"tasks": "hello"'
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertEqual(result, {"tasks": "hello"})

    def test_unrepairable_json_raises_value_error(self):
        raw = 'not json at all'
        with self.assertRaises(ValueError) as cm:
            AbilityManager._parse_tool_arguments(raw)
        self.assertIn("Invalid tool arguments JSON", str(cm.exception))
        self.assertIn("not json at all", str(cm.exception))

    def test_bare_text_repair_takes_priority_over_bracket_repair_failure(self):
        raw = '{"tasks": 扫描所有页面}'
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertEqual(result["tasks"], "扫描所有页面")

    def test_combined_bracket_and_bare_text_repair(self):
        raw = '{"tasks": scan all pages'
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertEqual(result, {"tasks": "scan all pages"})

    def test_real_bug_todo_create_bare_chinese(self):
        raw = (
            '{"tasks": 扫描模板PPT色彩方案;'
            '读取两份PPT的HTML页面结构和CSS变量;'
            '批量修改部门级PPT的背景色和主题色;'
            '批量修改班组级PPT的背景色和主题色;'
            '重新生成两份PPT文件}'
        )
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertIsInstance(result, dict)
        self.assertIn("tasks", result)
        self.assertIn("扫描模板PPT色彩方案", result["tasks"])

    def test_real_bug_todo_create_bare_chinese_second(self):
        raw = (
            '{"tasks": 扫描page-1到page-8，提取标题、百分比、'
            '图表、数据来源、事故案例等信息;'
            '扫描page-9到page-16，提取同样信息;'
            '扫描page-17到page-24，提取同样信息;'
            '扫描page-25到page-32，提取同样信息;'
            '汇总所有页面信息清单}'
        )
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertIsInstance(result, dict)
        self.assertIn("tasks", result)

    def test_empty_string_raises_value_error(self):
        with self.assertRaises(ValueError):
            AbilityManager._parse_tool_arguments("")

    def test_value_error_includes_raw_arguments(self):
        raw = '{broken json'
        with self.assertRaises(ValueError) as cm:
            AbilityManager._parse_tool_arguments(raw)
        self.assertIn("{broken json", str(cm.exception))


class TestParseToolArgumentsWithRepair(unittest.TestCase):
    """Tests for AbilityManager._parse_tool_arguments_with_repair."""

    def test_valid_json_no_repair(self):
        raw = '{"tasks": "hello"}'
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair(raw)
        self.assertEqual(parsed, {"tasks": "hello"})
        self.assertIsNone(repaired)

    def test_non_string_input(self):
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair(None)
        self.assertIsNone(parsed)
        self.assertIsNone(repaired)

    def test_dict_input(self):
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair({"a": 1})
        self.assertEqual(parsed, {"a": 1})
        self.assertIsNone(repaired)

    def test_list_input(self):
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair([1, 2])
        self.assertEqual(parsed, [1, 2])
        self.assertIsNone(repaired)

    def test_bare_text_repair_returns_repaired_string(self):
        raw = '{"tasks": scan all pages}'
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair(raw)
        self.assertEqual(parsed, {"tasks": "scan all pages"})
        self.assertIsNotNone(repaired)
        self.assertIn('"scan all pages"', repaired)

    def test_bracket_repair_returns_repaired_string(self):
        raw = '{"tasks": "hello"'
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair(raw)
        self.assertEqual(parsed, {"tasks": "hello"})
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired, '{"tasks": "hello"}')

    def test_bare_text_repair_after_bracket_repair_failure(self):
        raw = '{"tasks": 扫描所有页面}'
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair(raw)
        self.assertEqual(parsed["tasks"], "扫描所有页面")
        self.assertIsNotNone(repaired)

    def test_bare_text_repair_uses_bracket_repaired_text(self):
        raw = '{"tasks": scan all pages'
        parsed, repaired = AbilityManager._parse_tool_arguments_with_repair(raw)
        self.assertEqual(parsed, {"tasks": "scan all pages"})
        self.assertIsNotNone(repaired)

    def test_unrepairable_raises_value_error(self):
        raw = 'not json at all'
        with self.assertRaises(ValueError):
            AbilityManager._parse_tool_arguments_with_repair(raw)


class TestNoSilentEmptyDict(unittest.TestCase):
    """Regression: _parse_tool_arguments must NEVER silently return {}.

    Before the fix, invalid JSON was silently swallowed and an empty dict
    was returned, causing the tool to receive no arguments and fail
    repeatedly, eventually triggering the circuit breaker.
    """

    def test_bare_text_does_not_return_empty_dict(self):
        raw = '{"tasks": scan all pages}'
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertNotEqual(result, {})
        self.assertIn("tasks", result)

    def test_truncated_json_does_not_return_empty_dict(self):
        raw = '{"tasks": "hello world"'
        result = AbilityManager._parse_tool_arguments(raw)
        self.assertNotEqual(result, {})
        self.assertIn("tasks", result)

    def test_completely_invalid_json_raises(self):
        raw = 'this is not json'
        with self.assertRaises(ValueError):
            AbilityManager._parse_tool_arguments(raw)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
