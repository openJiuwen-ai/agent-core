# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for lone-surrogate handling in the OpenAI model client.

GitCode issue #1998: a lone surrogate (e.g. half an emoji from upstream
UTF-16 truncation) used to (1) fail request serialization but be wrapped as
a model failure, (2) break the error-log record itself, and (3) pass into the
request body un-sanitized.
"""

import json

import pytest

from openjiuwen.core.foundation.llm.model_clients.openai_model_client import _sanitize_lone_surrogates


def test_sanitize_replaces_lone_surrogate_in_leaf_str():
    # \ud83d is the high surrogate half of 🔴 (U+1F534)
    text = json.loads('"| 风险等级 | \\ud83d"')
    out = _sanitize_lone_surrogates(text)
    assert out == "| 风险等级 | \ufffd"
    assert "\ud83d" not in out


def test_sanitize_recurses_into_dict_and_list():
    payload = {
        "role": "user",
        "content": json.loads('"\\ud83d"'),
        "extra": [json.loads('"x\\udfff"')],
    }
    out = _sanitize_lone_surrogates(payload)
    assert out["content"] == "\ufffd"
    assert out["extra"] == ["x\ufffd"]


def test_sanitize_leaves_valid_emoji_untouched():
    # A real emoji is a single code point and must survive intact.
    assert _sanitize_lone_surrogates("done 🔴 ok") == "done 🔴 ok"


def test_sanitize_passes_through_non_str_non_container():
    assert _sanitize_lone_surrogates(42) == 42
    assert _sanitize_lone_surrogates(None) is None
