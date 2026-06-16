# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens_details": {"cached_tokens": 123}}, 123),
        ({"promptTokensDetails": {"cachedTokens": 124}}, 124),
        ({"input_tokens_details": {"cached_tokens": 125}}, 125),
        ({"input_token_details": {"cached_tokens": 126}}, 126),
        ({"inputTokensDetails": {"cachedTokens": 127}}, 127),
        ({"prompt_cache_hit_tokens": 456, "prompt_cache_miss_tokens": 789}, 456),
        ({"cache_read_input_tokens": 111, "cache_creation_input_tokens": 222}, 111),
        ({"cachedContentTokenCount": 333}, 333),
        ({"cached_content_token_count": 334}, 334),
        ({"cache_tokens": 444}, 444),
        ({"cached_tokens": 445}, 445),
        ({"cache_hit_tokens": 446}, 446),
        ({"cached_input_tokens": 447}, 447),
        ({"cache_read_tokens": 448}, 448),
        ({"prompt_cache_tokens": 449}, 449),
        ({"prompt_cached_tokens": 450}, 450),
        ({"prompt_cache_miss_tokens": 789}, 0),
        ({"cache_write_tokens": 222}, 0),
        ({"cache_creation_input_tokens": 222}, 0),
        ({}, 0),
        (None, 0),
    ],
)
def test_extract_cache_tokens_from_dict_shapes(usage, expected):
    assert BaseModelClient._extract_cache_tokens(usage) == expected


def test_extract_cache_tokens_from_object_shape():
    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cached_tokens="321"),
    )

    assert BaseModelClient._extract_cache_tokens(usage) == 321


def test_extract_cache_tokens_prefers_specific_hit_fields():
    usage = {
        "prompt_tokens_details": {"cached_tokens": 10},
        "prompt_cache_hit_tokens": 20,
        "cache_tokens": 30,
    }

    assert BaseModelClient._extract_cache_tokens(usage) == 10


def test_extract_cache_tokens_ignores_invalid_values():
    usage = {
        "prompt_tokens_details": {"cached_tokens": "not-a-number"},
        "prompt_cache_hit_tokens": False,
        "cache_tokens": -10,
    }

    assert BaseModelClient._extract_cache_tokens(usage) == 0


def test_extract_cache_tokens_ignores_mock_missing_attributes():
    usage = MagicMock()
    usage.prompt_tokens_details = None

    assert BaseModelClient._extract_cache_tokens(usage) == 0
