#!/usr/bin/env python
# coding: utf-8
"""Tests for create_browser_agent factory wiring."""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.browser_agent import (
    BROWSER_AGENT_FACTORY_NAME,
    DEFAULT_BROWSER_AGENT_MAX_ITERATIONS,
    DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT,
    DEFAULT_BROWSER_AGENT_TEMPERATURE,
    build_browser_agent_config,
    create_browser_agent,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    BrowserRunGuardrails,
    RuntimeSettings,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_capabilities import (
    CORE_BROWSER_TOOL_NAMES,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserRuntimeRail,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_working_context_rail import (
    BrowserWorkingContextRail,
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
    tools = [MagicMock() for _ in range(3)]
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
    mock_runtime_cls = stack.enter_context(patch("openjiuwen.harness.subagents.browser_agent.BrowserAgentRuntime"))
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


def test_browser_agent_registers_runtime_task_cleanup() -> None:
    calls, fake = _capture_create_deep_agent()
    ctx, runtime_cls, _build_tools, _tools = _patch_all(fake)
    with ctx:
        agent = create_browser_agent(_fake_model(), settings=_fake_settings())

    del calls
    agent.register_task_resource_cleanup.assert_called_once_with(
        runtime_cls.return_value.release_task_resources,
        prepare=runtime_cls.return_value.acquire_task_resources,
    )


def test_browser_agent_uses_independent_sampling_config_and_large_budget() -> None:
    parent_model = SimpleNamespace(
        model_client_config=SimpleNamespace(),
        model_config=ModelRequestConfig(
            model="test-model",
            temperature=0.95,
        ),
    )

    spec = build_browser_agent_config(
        parent_model,
        settings=_fake_settings(),
        language="en",
    )

    assert spec.model is not parent_model
    assert spec.model.model_config.temperature == DEFAULT_BROWSER_AGENT_TEMPERATURE
    assert parent_model.model_config.temperature == 0.95
    assert spec.max_iterations == DEFAULT_BROWSER_AGENT_MAX_ITERATIONS


def test_browser_agent_uses_independent_client_with_lower_temperature() -> None:
    parent_model = object.__new__(Model)
    parent_model.model_client_config = SimpleNamespace(client_provider="test")
    parent_model.model_config = ModelRequestConfig(
        model="test-model",
        temperature=0.95,
    )
    parent_model._client = SimpleNamespace(model_config=parent_model.model_config)

    def fake_model_init(self, model_client_config, model_config):
        self.model_client_config = model_client_config
        self.model_config = model_config
        self._client = SimpleNamespace(model_config=model_config)

    with patch.object(Model, "__init__", fake_model_init):
        spec = build_browser_agent_config(
            parent_model,
            settings=_fake_settings(),
            language="en",
        )

    assert spec.model is not parent_model
    assert spec.model._client is not parent_model._client
    assert spec.model._client.model_config.temperature == DEFAULT_BROWSER_AGENT_TEMPERATURE
    assert parent_model._client.model_config.temperature == 0.95


def test_browser_agent_prompt_enforces_convergent_browser_strategy() -> None:
    english = DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["en"]
    chinese = DEFAULT_BROWSER_AGENT_SYSTEM_PROMPT["cn"]

    assert "direct search-results URL" in english
    assert "browser_batch_interact" in english
    assert "observable condition waits" in english
    assert "navigate directly to that URL" in english
    assert "Stop immediately" in english
    assert "one runtime-maintained <browser_working_context>" in english
    assert "A fresh browser capture occurs initially" in english
    assert "runtime directive requires replanning" in english
    assert "直接构造搜索结果 URL" in chinese
    assert "可观察条件" in chinese
    assert "直接导航该 URL" in chinese
    assert "立即结束" in chinese
    assert "<browser_working_context>" in chinese
    assert "runtime 要求重新规划" in chinese
    assert "browser_run_code_unsafe" not in english
    assert "browser_run_code_unsafe" not in chinese
    assert "makes a browser_run_code tool visible" in english
    assert "工具可见" in chinese


def test_selected_capabilities_are_logged_and_forwarded_to_runtime(caplog) -> None:
    settings = _fake_settings()
    calls, fake = _capture_create_deep_agent()
    ctx, mock_runtime_cls, _mock_build, _tools = _patch_all(fake)
    caplog.set_level("INFO")

    with ctx:
        create_browser_agent(
            _fake_model(),
            settings=settings,
            browser_capabilities=["pdf"],
        )

    del calls
    allowed_tool_names = mock_runtime_cls.call_args.kwargs["allowed_tool_names"]
    assert "browser_click" in allowed_tool_names
    assert "browser_pdf_save" in allowed_tool_names
    assert "browser_mouse_click_xy" not in allowed_tool_names
    assert "requested=('pdf',)" in caplog.text
    assert "selected=('core', 'pdf')" in caplog.text
    assert "browser_pdf_save" in caplog.text


def test_unknown_capability_error_lists_rejected_and_available_names() -> None:
    with pytest.raises(ValueError) as exc_info:
        create_browser_agent(
            _fake_model(),
            browser_capabilities=["not-a-capability"],
        )

    message = str(exc_info.value)
    assert "Unsupported browser capabilities: not-a-capability" in message
    assert (
        "Available capabilities: core, advanced_code, unsafe_dev, pdf, vision, "
        "devtools, config, network, storage, testing"
    ) in message


def test_default_factory_always_forwards_core_allowlist() -> None:
    calls, fake = _capture_create_deep_agent()
    ctx, mock_runtime_cls, _mock_build, _tools = _patch_all(fake)

    with ctx:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    del calls
    assert mock_runtime_cls.call_args.kwargs["allowed_tool_names"] == CORE_BROWSER_TOOL_NAMES


@pytest.mark.parametrize(
    ("capability", "included", "excluded"),
    [
        ("advanced_code", "browser_run_code", "browser_run_code_unsafe"),
        ("unsafe_dev", "browser_run_code_unsafe", "browser_run_code"),
    ],
)
def test_factory_exposes_only_selected_run_code_variant(
    capability: str,
    included: str,
    excluded: str,
) -> None:
    calls, fake = _capture_create_deep_agent()
    ctx, mock_runtime_cls, _mock_build, _tools = _patch_all(fake)

    with ctx:
        create_browser_agent(
            _fake_model(),
            settings=_fake_settings(),
            browser_capabilities=[capability],
        )

    del calls
    allowed = mock_runtime_cls.call_args.kwargs["allowed_tool_names"]
    assert included in allowed
    assert excluded not in allowed


def test_default_wiring_main_agent_card_is_browser_agent() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    assert calls[0]["card"].name == "browser_agent"


def test_default_browser_agent_identity_is_stable_across_reconstruction() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())
        create_browser_agent(_fake_model(), settings=_fake_settings())

    assert calls[0]["card"].id == calls[1]["card"].id


def test_default_wiring_main_agent_has_no_subagents() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    assert calls[0].get("subagents", []) in (None, [])


def test_default_wiring_main_agent_receives_browser_helper_tools() -> None:
    calls, fake = _capture_create_deep_agent()
    fake_tools = _make_fake_tools()
    with _patch_all(fake, fake_tools=fake_tools)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    tool_cards = calls[0].get("tools", [])
    for tool in fake_tools:
        assert tool in tool_cards


def test_default_wiring_does_not_pre_register_playwright_mcp_on_subagent() -> None:
    calls, fake = _capture_create_deep_agent()
    settings = _fake_settings()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=settings)

    assert settings.mcp_cfg not in calls[0]["mcps"]


def test_default_wiring_main_agent_has_browser_runtime_rail() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    rails = calls[0].get("rails", [])
    assert any(isinstance(rail, BrowserRuntimeRail) for rail in rails)
    assert any(isinstance(rail, BrowserWorkingContextRail) for rail in rails)


def test_default_wiring_adds_browser_state_and_windows_large_tool_results() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    rails = calls[0].get("rails", [])
    context_rails = [rail for rail in rails if isinstance(rail, ContextProcessorRail)]
    assert len(context_rails) == 1

    processors = context_rails[0]._user_processors
    assert len(processors) == 3
    processor_map = dict(processors)
    config = processor_map["ToolResultWindowProcessor"]
    # Pin the intended contract literally (not against the source constant) so a
    # regression like the plain "browser_snapshot" name is actually caught.
    assert config.tool_names == [
        "browser_probe_interactives",
        "browser_probe_cards",
        "browser_snapshot",
        "browser_evaluate",
    ]
    assert config.keep_last_k == 1
    assert processor_map["BrowserWorkingContextProcessor"].max_recent_steps > 0
    assert processor_map["BrowserStateContextProcessor"].provider is not None
    assert config.trim_size == 1000
    assert config.min_offload_chars == 4096
    assert config.small_result_trim_size == 800


def test_working_context_processor_uses_browser_agent_language() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(
            _fake_model(),
            settings=_fake_settings(),
            language="cn",
        )

    context_rail = next(rail for rail in calls[0]["rails"] if isinstance(rail, ContextProcessorRail))
    processor_map = dict(context_rail._user_processors)
    assert processor_map["BrowserWorkingContextProcessor"].language == "cn"


def test_caller_context_processor_rail_is_augmented_with_browser_state() -> None:
    calls, fake = _capture_create_deep_agent()
    caller_rail = ContextProcessorRail(preset=False)
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings(), rails=[caller_rail])

    rails = calls[0].get("rails", [])
    context_rails = [rail for rail in rails if isinstance(rail, ContextProcessorRail)]
    # Only the caller's rail is present, and it receives the required live state
    # processor rather than competing with a second context rail.
    assert context_rails == [caller_rail]
    assert [key for key, _ in caller_rail._user_processors] == [
        "BrowserStateContextProcessor",
        "BrowserWorkingContextProcessor",
    ]


def test_default_wiring_does_not_add_sys_operation_rail() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    rails = calls[0].get("rails", [])
    assert not any(type(rail).__name__ == "SysOperationRail" for rail in rails)


def test_default_wiring_adds_scoped_offload_recall_without_filesystem_tools() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_all(fake)[0]:
        create_browser_agent(_fake_model(), settings=_fake_settings())

    tool_names = [
        getattr(getattr(tool, "card", None), "name", None)
        for tool in calls[0].get("tools", [])
    ]
    assert "browser_recall_offload" in tool_names
    assert "read_file" not in tool_names
    assert "bash" not in tool_names
    assert "powershell" not in tool_names


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
        instance=settings.instance,
        allowed_tool_names=CORE_BROWSER_TOOL_NAMES,
    )


def _model_without_client_config() -> SimpleNamespace:
    # model_client_config=None routes _resolve_runtime_settings through
    # build_runtime_settings(instance), exercising the keyed-instance path.
    return SimpleNamespace(model_client_config=None, model_config=None)


def test_browser_key_threads_keyed_instance_to_runtime() -> None:
    """A bare browser_key becomes a keyed BrowserInstanceConfig on the runtime."""
    calls, fake = _capture_create_deep_agent()
    ctx, mock_runtime_cls, _mock_build, _tools = _patch_all(fake)
    with ctx:
        create_browser_agent(_model_without_client_config(), browser_key="teammate-A")

    del calls
    instance = mock_runtime_cls.call_args.kwargs["instance"]
    assert instance is not None
    assert instance.key == "teammate-A"
    # The MCP server_id carried into the runtime must be isolated by the key.
    assert mock_runtime_cls.call_args.kwargs["mcp_cfg"].server_id.endswith("__teammate-A")


def test_browser_instance_dict_threads_port_to_runtime() -> None:
    """A serializable instance dict (teams-wire form) rebuilds into the runtime."""
    calls, fake = _capture_create_deep_agent()
    ctx, mock_runtime_cls, _mock_build, _tools = _patch_all(fake)
    with ctx:
        create_browser_agent(
            _model_without_client_config(),
            browser_instance={"key": "B", "managed_port": 9502, "driver_mode": "managed"},
        )

    del calls
    instance = mock_runtime_cls.call_args.kwargs["instance"]
    assert instance.key == "B"
    assert instance.managed_port == 9502
    assert instance.driver_mode == "managed"


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
