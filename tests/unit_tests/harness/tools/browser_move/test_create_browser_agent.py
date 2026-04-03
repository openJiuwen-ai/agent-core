#!/usr/bin/env python
# coding: utf-8
"""Tests for create_browser_agent factory wiring."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.browser_agent import (
    BROWSER_AGENT_FACTORY_NAME,
    DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT,
    build_browser_agent_config,
    create_browser_agent,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserRunGuardrails,
    RuntimeSettings,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserRuntimeRail,
)


def _fake_model() -> MagicMock:
    return MagicMock(spec=Model)


def _fake_settings() -> RuntimeSettings:
    mcp_cfg = McpServerConfig(
        server_id="test",
        server_name="test",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": "."},
    )
    return RuntimeSettings(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )


def _make_fake_tools():
    tools = [MagicMock() for _ in range(4)]
    for tool in tools:
        tool.card = MagicMock()
    return tools


def _capture_create_deep_agent():
    calls: list[dict] = []

    def fake(**kwargs):
        agent = MagicMock()
        agent.card = kwargs.get("card")
        calls.append(kwargs)
        return agent

    return calls, fake


def _patch_all(fake_create, *, runtime_mock=None, fake_tools=None):
    stack = ExitStack()
    mock_runtime_cls = stack.enter_context(
        patch("openjiuwen.harness.subagents.browser_agent.BrowserAgentRuntime")
    )
    if runtime_mock is not None:
        mock_runtime_cls.return_value = runtime_mock
    tools = fake_tools if fake_tools is not None else _make_fake_tools()
    mock_build_tools = stack.enter_context(
        patch("openjiuwen.harness.subagents.browser_agent.build_browser_runtime_tools", return_value=tools)
    )
    stack.enter_context(
        patch(
            "openjiuwen.harness.subagents.browser_agent.create_deep_agent",
            side_effect=fake_create,
        )
    )
    return stack, mock_runtime_cls, mock_build_tools, tools


def test_default_wiring_creates_one_agent() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    assert len(calls) == 1


def test_default_wiring_main_agent_card_is_browser_agent() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    assert calls[0]["card"].name == "browser_agent"


def test_default_wiring_main_agent_has_no_subagents() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    assert calls[0].get("subagents", []) in (None, [])


def test_default_wiring_main_agent_receives_four_browser_tool_cards() -> None:
    calls, fake = _capture_create_deep_agent()
    fake_tools = _make_fake_tools()
    with _patch_all(fake, fake_tools=fake_tools)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    tool_cards = calls[0].get("tools", [])
    for tool in fake_tools:
        assert tool in tool_cards


def test_default_wiring_main_agent_has_browser_runtime_rail() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    rails = calls[0].get("rails", [])
    assert any(isinstance(rail, BrowserRuntimeRail) for rail in rails)


def test_default_wiring_build_tools_called_with_runtime_instance() -> None:
    calls, fake = _capture_create_deep_agent()
    ctx, mock_runtime_cls, mock_build_tools, _tools = _patch_all(fake)
    with ctx:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    del calls
    mock_build_tools.assert_called_once()
    assert mock_build_tools.call_args.args[0] is mock_runtime_cls.return_value


def test_custom_subagents_are_forwarded() -> None:
    custom = MagicMock()
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), subagents=[custom], settings=_fake_settings())

    assert calls[0]["subagents"] == [custom]


def test_settings_forwarded_to_runtime_constructor() -> None:
    settings = _fake_settings()
    calls, fake = _capture_create_deep_agent()
    ctx, mock_runtime_cls, _mock_build, _tools = _patch_all(fake)
    with ctx:
        create_browser_agent(_fake_model(), settings=settings)

    del calls
    mock_runtime_cls.assert_called_once_with(
        provider=settings.provider,
        api_key=settings.api_key,
        api_base=settings.api_base,
        model_name=settings.model_name,
        mcp_cfg=settings.mcp_cfg,
        guardrails=settings.guardrails,
    )


def test_language_en_uses_english_prompt() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), language="en", settings=_fake_settings())

    assert calls[0]["system_prompt"] == DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["en"]


def test_language_cn_uses_chinese_prompt() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), language="cn", settings=_fake_settings())

    assert calls[0]["system_prompt"] == DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["cn"]


def test_user_tools_are_merged_with_browser_tools() -> None:
    user_tool = MagicMock()
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), tools=[user_tool], settings=_fake_settings())

    tool_cards = calls[0].get("tools", [])
    assert user_tool in tool_cards
    assert len(tool_cards) == 5


def test_build_browser_agent_config_uses_browser_factory() -> None:
    settings = _fake_settings()
    spec = build_browser_agent_config(_fake_model(), settings=settings, language="en")

    assert isinstance(spec, SubAgentConfig)
    assert spec.agent_card.name == "browser_agent"
    assert spec.system_prompt == DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["en"]
    assert spec.factory_name == BROWSER_AGENT_FACTORY_NAME
    assert spec.factory_kwargs["settings"] == settings


def test_build_browser_agent_config_fallback_uses_model_name_field() -> None:
    model = MagicMock(spec=Model)
    model.model_client_config = MagicMock(
        client_provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
    )
    model.model_config = MagicMock()
    del model.model_config.model
    model.model_config.model_name = "test-model-name"

    spec = build_browser_agent_config(model, language="en")

    assert spec.factory_kwargs["settings"].model_name == "test-model-name"
