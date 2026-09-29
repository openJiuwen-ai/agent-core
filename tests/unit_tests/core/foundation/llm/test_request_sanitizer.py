# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Wire-boundary regressions for dictionary arguments and UTF-16 fragments."""

import copy
import json

import pytest
from openjiuwen.core.foundation.llm.utils.request_sanitizer import (
    clean_unicode,
    sanitize_chat_request,
)
from openjiuwen.core.foundation.llm.utils.responses_utils import build_request_body
from tests.unit_tests.core.foundation.llm.test_openai_model_client import _make_client


def history():
    return [
        {"role": "user", "content": "title \ud83d\ude00 cut \ud83d"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call1",
                    "type": "function",
                    "function": {
                        "name": "browser_batch_interact",
                        "arguments": {"steps": [{"op": "fill", "value": "中文"}]},
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call1", "content": "page \udc00", "name": "browser_batch_interact"},
    ]


def test_sanitizer_preserves_valid_unicode_literal_escapes_and_does_not_mutate_history():
    messages = history()
    before = copy.deepcopy(messages)
    result = sanitize_chat_request({"messages": messages, "extra_body": {"hint": "\udfff"}})
    assert result["messages"][0]["content"] == "title 😀 cut �"
    args = result["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(args) == before[1]["tool_calls"][0]["function"]["arguments"]
    assert clean_unicode(r"literal \ud83d") == r"literal \ud83d"
    assert messages == before
    json.dumps(result, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("backend", ["chat", "responses"])
def test_actual_request_builders_accept_the_browser_recovery_history(backend):
    messages = history()
    if backend == "responses":
        result = build_request_body(model="local-fixture", messages=messages)
        arguments = next(item["arguments"] for item in result["input"] if item.get("type") == "function_call")
    else:
        client = _make_client()
        result = client._build_request_params(
            messages=messages,
            stream=False,
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
        )
        arguments = result["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(arguments, str)
    assert json.loads(arguments)["steps"][0]["value"] == "中文"
    json.dumps(result, ensure_ascii=False).encode("utf-8")
