# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AbilityManager tool-argument JSON parsing."""

from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.ability_manager import AbilityManager


def test_blank_tool_arguments_parse_as_empty_object():
    parsed, repaired = AbilityManager._parse_tool_arguments_with_repair("")
    assert parsed == {}
    assert repaired is None

    parsed_ws, repaired_ws = AbilityManager._parse_tool_arguments_with_repair("   \n")
    assert parsed_ws == {}
    assert repaired_ws is None


def test_valid_json_object_still_parses():
    parsed, repaired = AbilityManager._parse_tool_arguments_with_repair('{"a": 1}')
    assert parsed == {"a": 1}
    assert repaired is None


def test_invalid_json_still_raises():
    with pytest.raises(ValueError, match="Invalid tool arguments JSON"):
        AbilityManager._parse_tool_arguments_with_repair('{"query": "unterminated}')
