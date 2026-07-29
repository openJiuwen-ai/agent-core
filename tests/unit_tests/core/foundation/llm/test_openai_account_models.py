# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from pathlib import Path

import httpx

from openjiuwen.extensions.external_provider.openai_auth.openai_account_models import (
    OPENAI_ACCOUNT_MODELS_CLIENT_VERSION,
    DEFAULT_OPENAI_ACCOUNT_MODELS,
    OpenAIAccountModelCatalog,
    default_openai_account_models_cache_path,
    parse_openai_account_model_ids,
)


def _cache_path(tmp_path: Path) -> Path:
    return tmp_path / "openai_account_models_cache.json"


def test_default_openai_account_models_cache_path_uses_openjiuwen_home(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENJIUWEN_OPENAI_ACCOUNT_MODELS_CACHE_FILE", raising=False)
    monkeypatch.delenv("OPENJIUWEN_AUTH_FILE", raising=False)
    monkeypatch.setenv("OPENJIUWEN_HOME", str(tmp_path))

    assert default_openai_account_models_cache_path() == tmp_path / "openai_account_models_cache.json"


def test_parse_openai_account_model_ids_does_not_filter_supported_in_api():
    model_ids = parse_openai_account_model_ids(
        {
            "models": [
                {"slug": "hidden-model", "visibility": "hide", "priority": 0},
                {"slug": "gpt-5.4", "priority": 20},
                {"slug": "api-disabled-visible", "supported_in_api": False, "priority": 10},
                {"slug": "gpt-5.3-codex-spark", "supported_in_api": False, "priority": 1},
            ]
        }
    )

    assert model_ids[:3] == ["gpt-5.3-codex-spark", "api-disabled-visible", "gpt-5.4"]
    assert "hidden-model" not in model_ids
    assert "gpt-5.5" in model_ids


def test_catalog_fetches_models_with_openai_account_token_and_writes_cache(tmp_path):
    seen_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_request["url"] = request.url
        seen_request["headers"] = request.headers
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-5.4", "priority": 10},
                    {"slug": "gpt-5.3-codex", "priority": 20},
                ]
            },
        )

    catalog = OpenAIAccountModelCatalog(
        cache_path=_cache_path(tmp_path),
        transport=httpx.MockTransport(handler),
        now=lambda: 1000.0,
    )

    model_ids = catalog.list_model_ids(access_token="access-token")

    assert seen_request["url"].path == "/backend-api/codex/models"
    assert seen_request["url"].params["client_version"] == OPENAI_ACCOUNT_MODELS_CLIENT_VERSION
    assert seen_request["headers"]["Authorization"] == "Bearer access-token"
    assert model_ids[:2] == ["gpt-5.4", "gpt-5.3-codex"]

    cache = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
    assert cache["provider"] == "OpenAIAccount"
    assert cache["fetched_at"] == 1000.0
    assert cache["model_ids"][:2] == ["gpt-5.4", "gpt-5.3-codex"]


def test_catalog_uses_cache_when_live_request_fails(tmp_path):
    _cache_path(tmp_path).write_text(
        json.dumps({"model_ids": ["cached-model"]}),
        encoding="utf-8",
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"error": "failed"})

    catalog = OpenAIAccountModelCatalog(
        cache_path=_cache_path(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    assert catalog.list_model_ids(access_token="access-token") == ["cached-model"]
    assert len(calls) == 1


def test_catalog_uses_fallback_without_token_or_cache(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"models": [{"slug": "live"}]})

    catalog = OpenAIAccountModelCatalog(
        cache_path=_cache_path(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    assert catalog.list_model_ids() == DEFAULT_OPENAI_ACCOUNT_MODELS
    assert calls == []
