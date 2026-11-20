# coding: utf-8
"""Tests for the attribution Jev facade: settings resolution and request routing."""

from __future__ import annotations

import json

import httpx
import pytest

from openjiuwen.agent_evolving.attribution.jev import (
    DEFAULT_JEV_SETTINGS,
    JevClient,
    resolve_jev_settings,
)
from openjiuwen.core.foundation.llm.system_one import NoulQuestion

_JEV_ENV_KEYS = tuple(DEFAULT_JEV_SETTINGS)


@pytest.fixture(autouse=True)
def _clear_jev_env(monkeypatch):
    for key in _JEV_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _set_env(monkeypatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_client_uses_jev_env_settings_when_no_arguments_given(monkeypatch):
    _set_env(
        monkeypatch,
        JEV_BASE_URL="https://jev.example.internal/",
        JEV_MODEL="jev-latest",
        JEV_API_KEY="sk-from-env",
    )

    client = JevClient()

    assert client._client.api_base == "https://jev.example.internal"
    assert client._client.model_name == "jev-latest"
    assert client._client._api_key == "sk-from-env"


def test_explicit_arguments_override_jev_env_settings(monkeypatch):
    _set_env(monkeypatch, JEV_BASE_URL="https://jev.example.internal", JEV_MODEL="jev-latest")

    client = JevClient(base_url="http://127.0.0.1:9000", model="jev-preview", api_key="sk-explicit")

    assert client._client.api_base == "http://127.0.0.1:9000"
    assert client._client.model_name == "jev-preview"
    assert client._client._api_key == "sk-explicit"


def test_in_repo_defaults_apply_without_env_or_arguments():
    client = JevClient()

    assert client._client.api_base == DEFAULT_JEV_SETTINGS["JEV_BASE_URL"]
    assert client._client.model_name == DEFAULT_JEV_SETTINGS["JEV_MODEL"]
    assert client._client._api_key == "local-kev-placeholder"


def test_empty_jev_env_values_fall_back_to_defaults(monkeypatch):
    _set_env(monkeypatch, JEV_BASE_URL="", JEV_MODEL="")

    client = JevClient()

    assert resolve_jev_settings()["JEV_BASE_URL"] == DEFAULT_JEV_SETTINGS["JEV_BASE_URL"]
    assert client._client.api_base == DEFAULT_JEV_SETTINGS["JEV_BASE_URL"]
    assert client._client.model_name == DEFAULT_JEV_SETTINGS["JEV_MODEL"]


@pytest.mark.asyncio
async def test_request_goes_to_the_env_configured_endpoint(monkeypatch):
    _set_env(
        monkeypatch,
        JEV_BASE_URL="https://jev.example.internal",
        JEV_MODEL="jev-latest",
        JEV_API_KEY="sk-from-env",
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {"done": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = JevClient()
    await client._client.aclose()
    client._client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        answers = await client.system_one("task log", {"done": NoulQuestion(instructions="Is the task done?")})
    finally:
        await client.close()

    assert answers["done"]["noul"] == 0.9
    assert len(seen) == 1
    assert str(seen[0].url) == "https://jev.example.internal/v1/systemone"
    assert json.loads(seen[0].content)["model"] == "jev-latest"
    assert seen[0].headers["Authorization"] == "Bearer sk-from-env"
