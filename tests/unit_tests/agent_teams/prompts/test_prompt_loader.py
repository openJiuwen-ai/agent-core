# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for agent-team prompt template loading."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.prompts.loader import _load, load_template


@pytest.fixture(autouse=True)
def _clear_prompt_cache():
    _load.cache_clear()
    yield
    _load.cache_clear()


def test_load_template_normalizes_zh_to_cn_for_org_unclaimed_expired():
    zh = load_template("org_unclaimed_expired", "zh")
    cn = load_template("org_unclaimed_expired", "cn")
    assert zh.content == cn.content
    assert "org_create_task" in zh.content or "recreation_request_id" in zh.content
