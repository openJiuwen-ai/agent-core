# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AbilityManager tool-argument JSON parsing/repair."""

from __future__ import annotations

import json

import pytest

from openjiuwen.core.single_agent.ability_manager import AbilityManager


_parse = AbilityManager._parse_tool_arguments_with_repair
_repair = AbilityManager._repair_tool_arguments_json


def test_repair_balances_unclosed_brackets() -> None:
    repaired = _repair('{"action": "add", "name": "x"')
    assert repaired == '{"action": "add", "name": "x"}'
    assert json.loads(repaired) == {"action": "add", "name": "x"}


def test_repair_closes_string_cut_mid_value() -> None:
    """Regression: a stream truncated inside a string value previously returned
    None (unrecoverable). Closing the dangling quote yields valid JSON with the
    partial value, which downstream strict validation can then reject with a
    precise field error instead of a generic malformed-arguments failure."""
    repaired = _repair('{"action": "add", "cron_expr": "0 9 * *')
    assert repaired is not None
    assert json.loads(repaired) == {"action": "add", "cron_expr": "0 9 * *"}


def test_repair_incident_20260920_cron_add_truncated() -> None:
    """Exact payload from a production incident log:

    Tool 'cron' got malformed arguments: Invalid tool arguments JSON:
    Unterminated string starting at: line 1 column 32 (char 31).
    Raw arguments: '{"action": "add", "cron_expr": "/5'

    The model's stream was cut mid-value while emitting cron_expr '*/5 * * * *'.
    The repairer must close the dangling string; downstream cron-expression
    validation then returns a precise field error the model can self-correct,
    instead of a generic malformed-arguments failure."""
    raw = '{"action": "add", "cron_expr": "/5'
    parsed, repaired = _parse(raw)
    assert parsed == {"action": "add", "cron_expr": "/5"}
    assert repaired == '{"action": "add", "cron_expr": "/5"}'


def test_repair_closes_string_then_brackets() -> None:
    repaired = _repair('{"a": {"b": "unclosed')
    assert repaired == '{"a": {"b": "unclosed"}}'
    assert json.loads(repaired) == {"a": {"b": "unclosed"}}


def test_repair_unterminated_escape_still_rejected() -> None:
    """A trailing backslash would escape the injected closing quote, so the
    text is genuinely unrecoverable."""
    assert _repair('{"a": "ends with backslash\\') is None


def test_repair_balanced_text_returned_as_is() -> None:
    assert _repair('{"a": 1}') == '{"a": 1}'


def test_repair_mismatched_bracket_rejected() -> None:
    assert _repair('{"a": 1]') is None


def test_repair_empty_text_rejected() -> None:
    assert _repair('   ') is None


def test_parse_returns_repaired_arguments() -> None:
    parsed, repaired = _parse('{"action": "add", "name": "x"')
    assert parsed == {"action": "add", "name": "x"}
    assert repaired == '{"action": "add", "name": "x"}'


def test_parse_valid_json_returns_none_repair() -> None:
    parsed, repaired = _parse('{"action": "list"}')
    assert parsed == {"action": "list"}
    assert repaired is None


def test_parse_garbage_raises_value_error() -> None:
    with pytest.raises(ValueError):
        _parse('{"a": "b\\')
