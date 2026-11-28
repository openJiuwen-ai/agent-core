# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


def test_arguments_dict_is_normalized_to_json_string():
    call = ToolCall(id="1", type="function", name="bash", arguments={"command": "pwd"})

    assert isinstance(call.arguments, str)
    assert json.loads(call.arguments) == {"command": "pwd"}


def test_arguments_list_is_normalized_to_json_string():
    call = ToolCall(id="1", type="function", name="bash", arguments=[{"command": "pwd"}])

    assert isinstance(call.arguments, str)
    assert json.loads(call.arguments) == [{"command": "pwd"}]


def test_arguments_string_is_unchanged():
    raw = '{"command": "pwd"}'

    call = ToolCall(id="1", type="function", name="bash", arguments=raw)

    assert call.arguments == raw


def test_unsupported_arguments_type_still_fails():
    with pytest.raises(ValidationError):
        ToolCall(id="1", type="function", name="bash", arguments=123)
