# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""prefer_natural_or_structured_text — shared schema-path text policy."""
from __future__ import annotations

import json

from openjiuwen.agent_teams.workflow.backends._result_text import (
    prefer_natural_or_structured_text,
)


def test_prefers_non_empty_natural_text():
    assert prefer_natural_or_structured_text("hello", {"a": 1}) == "hello"
    assert prefer_natural_or_structured_text("  keep me  ", {"a": 1}) == "  keep me  "


def test_falls_back_to_json_when_natural_missing():
    structured = {"answer": 42}
    assert prefer_natural_or_structured_text("", structured) == json.dumps(
        structured, ensure_ascii=False
    )
    assert prefer_natural_or_structured_text("   ", structured) == json.dumps(
        structured, ensure_ascii=False
    )
    assert prefer_natural_or_structured_text(None, structured) == json.dumps(
        structured, ensure_ascii=False
    )
