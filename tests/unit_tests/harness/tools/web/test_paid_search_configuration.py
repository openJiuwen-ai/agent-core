# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Paid-search exposure and execution must use the configured providers only."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.tools.web import WebPaidSearchTool, _http, create_web_tools

PROVIDERS = ("perplexity", "bocha", "jina", "serper")


@pytest.fixture(autouse=True)
def isolated_search_env(monkeypatch):
    for provider in PROVIDERS:
        monkeypatch.delenv(f"{provider.upper()}_API_KEY", raising=False)
    for key in ("PAID_SEARCH_PROVIDER", "WEB_PAID_SEARCH_PROVIDER"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def runners(monkeypatch):
    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(_http, "new_session", session)
    request = AsyncMock(side_effect=AssertionError("Real HTTP must not be used"))
    monkeypatch.setattr(_http, "request", request)
    mocks = {}
    for provider in PROVIDERS:
        runner = AsyncMock(return_value={"answer": provider, "urls": []})
        monkeypatch.setattr(WebPaidSearchTool, f"_{provider}_search", runner)
        mocks[provider] = runner
    yield mocks
    request.assert_not_awaited()


@pytest.mark.parametrize("language", ["cn", "en"])
@pytest.mark.parametrize("provider", PROVIDERS)
def test_card_only_exposes_configured_provider(monkeypatch, language, provider):
    monkeypatch.setenv(f"{provider.upper()}_API_KEY", "test-key")
    card = WebPaidSearchTool(language=language).card
    schema = card.input_params["properties"]["provider"]
    assert schema["enum"] == ["auto", provider]
    assert schema["default"] == "auto"
    assert provider in card.description.lower()
    for unavailable in set(PROVIDERS) - {provider}:
        assert unavailable not in card.description.lower()
        assert unavailable not in schema["description"].lower()


def test_card_rebuild_uses_current_keys_without_changing_old_snapshot(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    old = WebPaidSearchTool().card
    monkeypatch.setenv("BOCHA_API_KEY", " \t ")
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    new = WebPaidSearchTool().card
    assert old.input_params["properties"]["provider"]["enum"] == ["auto", "bocha"]
    assert new.input_params["properties"]["provider"]["enum"] == ["auto", "serper"]


def test_no_keys_or_blank_keys_do_not_register_paid_search(monkeypatch):
    for provider in PROVIDERS:
        monkeypatch.setenv(f"{provider.upper()}_API_KEY", " \t ")
    tools = create_web_tools(include_free_search=False)
    assert [tool.card.name for tool in tools] == ["fetch_webpage"]
    assert WebPaidSearchTool().card.input_params["properties"]["provider"]["enum"] == ["auto"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_registered_provider_requests_use_current_key_and_return_results(monkeypatch, provider):
    key_name = f"{provider.upper()}_API_KEY"
    monkeypatch.setenv(key_name, "first-test-key")
    body = json.dumps({
        "summary": "answer", "webPages": {"value": [{"url": "https://example.invalid/result"}]},
        "choices": [{"message": {"content": "answer https://example.invalid/result"}}],
        "citations": ["https://example.invalid/result"],
        "organic": [{"link": "https://example.invalid/result"}],
    }).encode()
    request = AsyncMock(return_value=(200, {}, body, "https://example.invalid/search", False))
    monkeypatch.setattr(_http, "request", request)
    tools = create_web_tools(include_free_search=False)
    tool = next(item for item in tools if item.card.name == "paid_search")
    for key in ("first-test-key", "rotated-test-key"):
        monkeypatch.setenv(key_name, key)
        result = await tool.invoke({"query": "test"})
        assert f"Paid search provider: {provider}" in result
        assert "https://example.invalid/result" in result
        headers = request.call_args.kwargs["headers"]
        if provider == "serper":
            assert headers["X-API-KEY"] == key
        else:
            assert headers["Authorization"] == f"Bearer {key}"
    assert request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["auto", "serper"])
async def test_auto_and_stale_provider_only_dispatch_to_configured_runner(monkeypatch, runners, provider):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    result = await WebPaidSearchTool().invoke({"query": "test", "provider": provider})
    assert "Paid search provider: bocha" in result
    runners["bocha"].assert_awaited_once()
    for unavailable in ("serper", "jina", "perplexity"):
        runners[unavailable].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("env_key", ["PAID_SEARCH_PROVIDER", "WEB_PAID_SEARCH_PROVIDER"])
@pytest.mark.parametrize("override", ["serper", "unknown", "auto", " "])
async def test_unavailable_override_does_not_break_auto(monkeypatch, runners, env_key, override):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.setenv(env_key, override)
    result = await WebPaidSearchTool().invoke({"query": "test"})
    assert "Paid search provider: bocha" in result
    runners["bocha"].assert_awaited_once()
    runners["serper"].assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_override_and_explicit_selection_are_preserved(monkeypatch, runners):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setenv("PAID_SEARCH_PROVIDER", "serper")
    tool = WebPaidSearchTool()
    assert "provider: serper" in await tool.invoke({"query": "test"})
    assert "provider: bocha" in await tool.invoke({"query": "test", "provider": "bocha"})
    runners["bocha"].assert_awaited_once()
    runners["serper"].assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_skips_provider_removed_while_search_is_running(monkeypatch, runners):
    for provider in ("perplexity", "bocha", "serper"):
        monkeypatch.setenv(f"{provider.upper()}_API_KEY", "test-key")

    async def fail_and_remove_bocha(*args):
        monkeypatch.delenv("BOCHA_API_KEY")
        raise RuntimeError("provider failed")

    runners["perplexity"].side_effect = fail_and_remove_bocha
    result = await WebPaidSearchTool().invoke({"query": "test"})
    assert "provider: serper" in result
    runners["perplexity"].assert_awaited_once()
    runners["bocha"].assert_not_awaited()
    runners["jina"].assert_not_awaited()
    runners["serper"].assert_awaited_once()


@pytest.mark.asyncio
async def test_no_keys_does_not_open_http_session(monkeypatch):
    session = MagicMock(side_effect=AssertionError("No network session expected"))
    monkeypatch.setattr(_http, "new_session", session)
    result = await WebPaidSearchTool().invoke({"query": "test", "provider": "bocha"})
    assert "no paid search provider API key configured" in result
    session.assert_not_called()


def test_hot_reload_updates_paid_metadata_even_when_card_id_is_unchanged(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    old = WebPaidSearchTool(agent_id="test-agent").card
    monkeypatch.delenv("BOCHA_API_KEY")
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    new = WebPaidSearchTool(agent_id="test-agent").card
    assert old.id == new.id
    agent = object.__new__(DeepAgent)
    agent.ability_manager = MagicMock()
    agent.ability_manager.get.return_value = old
    agent._extension_bound_tool_names = lambda: set()
    agent._unregister_tool_resource = MagicMock()
    agent._ensure_builtin_tool_resource = MagicMock()
    config = SimpleNamespace(tools=[new])

    agent._hot_reload_tools(config, previous_tools=[old])

    agent._unregister_tool_resource.assert_called_once_with(old)
    agent.ability_manager.remove.assert_called_once_with("paid_search")
    agent.ability_manager.add.assert_called_once_with(new)
    agent._ensure_builtin_tool_resource.assert_called_once_with(new, config)


@pytest.mark.parametrize("name", ["paid_search", "other_tool"])
def test_hot_reload_keeps_unchanged_paid_card_and_other_tools(monkeypatch, name):
    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    old = WebPaidSearchTool(agent_id="test-agent").card
    old.name = name
    new = old.model_copy(deep=True)
    if name == "other_tool":
        new.description = "Custom metadata does not change existing reload behavior"
    agent = object.__new__(DeepAgent)
    agent.ability_manager = MagicMock()
    agent.ability_manager.get.return_value = old
    agent._extension_bound_tool_names = lambda: set()
    agent._unregister_tool_resource = MagicMock()
    agent._ensure_builtin_tool_resource = MagicMock()
    config = SimpleNamespace(tools=[new])

    agent._hot_reload_tools(config, previous_tools=[old])

    agent._unregister_tool_resource.assert_not_called()
    agent.ability_manager.add.assert_not_called()
    agent.ability_manager.remove.assert_not_called()
