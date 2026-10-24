# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for the search-tool pairing sentence in the fetch_webpage description."""

from __future__ import annotations

import pytest

from openjiuwen.harness.prompts.tools.web_tools import FetchWebpageMetadataProvider

_PAID_SEARCH_KEY_ENVS = ("PERPLEXITY_API_KEY", "BOCHA_API_KEY", "JINA_API_KEY", "SERPER_API_KEY")


@pytest.fixture(autouse=True)
def _no_search_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from a known state: no inherited search switch or API key from the developer shell."""
    monkeypatch.delenv("FREE_SEARCH_DDG_ENABLED", raising=False)
    monkeypatch.delenv("FREE_SEARCH_BING_ENABLED", raising=False)
    for key in _PAID_SEARCH_KEY_ENVS:
        monkeypatch.delenv(key, raising=False)


def _desc(language: str) -> str:
    return FetchWebpageMetadataProvider().get_description(language)


def test_no_search_enabled_mentions_neither_tool() -> None:
    for language in ("cn", "en"):
        text = _desc(language)
        assert "free_search" not in text
        assert "paid_search" not in text
    assert "通常配合" not in _desc("cn")
    assert "Usually used after" not in _desc("en")


def test_only_free_search_enabled_pairs_with_free_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    cn, en = _desc("cn"), _desc("en")
    assert "通常配合 free_search 使用" in cn
    assert "Usually used after free_search:" in en
    assert "paid_search" not in cn
    assert "paid_search" not in en


def test_only_paid_search_enabled_pairs_with_paid_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "dummy-key")
    cn, en = _desc("cn"), _desc("en")
    assert "通常配合 paid_search 使用" in cn
    assert "Usually used after paid_search:" in en
    assert "free_search" not in cn
    assert "free_search" not in en


def test_both_enabled_pair_with_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREE_SEARCH_BING_ENABLED", "on")
    monkeypatch.setenv("BOCHA_API_KEY", "dummy-key")
    assert "通常配合 paid_search 或 free_search 使用" in _desc("cn")
    assert "Usually used after paid_search or free_search:" in _desc("en")


def test_cn_description_states_the_pairing_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FREE_SEARCH_DDG_ENABLED", "true")
    cn = _desc("cn")
    assert cn.count("通常配合") == 1
    assert cn.count("先搜索") == 1
