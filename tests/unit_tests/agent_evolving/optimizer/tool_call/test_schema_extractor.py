# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.agent_evolving.optimizer.tool_call.utils.schema_extractor import extract_schema


def test_extract_schema_with_dict_and_nested_values():
    src = {
        "name": "tool",
        "parameters": {
            "type": "object",
            "properties": {"q": {"type": "string"}, "k": [1, 2]},
            "required": ["q"],
        },
        "enabled": True,
    }
    out = extract_schema(src)
    assert out["name"] == ""
    assert out["enabled"] == ""
    assert out["parameters"]["required"] == ["q"]
    assert out["parameters"]["properties"]["q"]["type"] == ""
    assert out["parameters"]["properties"]["k"] == [1, 2]


def test_extract_schema_with_json_string_and_invalid():
    out = extract_schema('{"a": 1, "b": {"c": 2}}')
    assert out == {"a": "", "b": {"c": ""}}

    assert extract_schema("not-json") == {}
