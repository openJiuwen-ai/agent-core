from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ModelClientConfig,
    ModelRequestConfig,
    SystemMessage,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


def _context():
    context = MagicMock()
    context.token_counter.return_value = None
    context.workspace_dir.return_value = ""
    context.session_id.return_value = "session-1"
    context.get_session_ref.return_value.get_state.return_value = {}
    return context


def _window():
    return ContextWindow(
        system_messages=[],
        context_messages=[
            UserMessage(content="Earlier request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content="Current request"),
        ],
        tools=[],
    )


def _client():
    return ModelClientConfig(client_provider="OpenAI", api_key="test-key", api_base="https://example.test/v1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_name", "force", "expected_name"),
    [
        ("", False, "desktop-selected"),
        ("compact-primary", False, "compact-primary"),
        ("compact-primary", True, "startup-model"),
    ],
)
async def test_auto_compression_selects_configured_or_current_model_without_changing_manual_path(
    configured_name, force, expected_name
):
    created = []

    def model_factory(client, request):
        created.append(request.model_name)
        model = MagicMock()
        model.invoke = AsyncMock(return_value=AssistantMessage(content="Compact state"))
        return model

    with patch("openjiuwen.core.context_engine.processor.forked.compressor.base.Model", side_effect=model_factory):
        compressor = DialogueCompressor(
            DialogueCompressorConfig(
                model=ModelRequestConfig(model_name="startup-model"),
                model_client=_client(),
                auto_compression={"model_name": configured_name},
            )
        )
        event, _ = await compressor.on_get_context_window(
            _context(),
            _window(),
            force=force,
            model_config=ModelRequestConfig(model_name="desktop-selected"),
            model_client_config=_client(),
        )

    assert event is not None
    assert created[-1] == expected_name


@pytest.mark.asyncio
async def test_auto_compression_falls_back_once_after_primary_timeout_retries():
    calls = []

    def model_factory(client, request):
        model = MagicMock()

        async def invoke(**kwargs):
            calls.append(request.model_name)
            if request.model_name != "compact-backup":
                raise TimeoutError("model timed out")
            return AssistantMessage(content="Backup compact state")

        model.invoke = invoke
        return model

    with patch("openjiuwen.core.context_engine.processor.forked.compressor.base.Model", side_effect=model_factory):
        compressor = DialogueCompressor(
            DialogueCompressorConfig(
                model=ModelRequestConfig(model_name="startup-model"),
                model_client=_client(),
                auto_compression={"model_name": "compact-primary", "fallback_model_name": "compact-backup"},
            )
        )
        event, _ = await compressor.on_get_context_window(
            _context(), _window(), model_config=ModelRequestConfig(model_name="desktop-selected"),
            model_client_config=_client(),
        )

    assert event is not None
    assert calls == ["compact-primary", "compact-primary", "compact-primary", "compact-backup"]


@pytest.mark.asyncio
async def test_auto_compression_does_not_initialize_backup_when_primary_succeeds():
    created = []

    def model_factory(client, request):
        created.append(request.model_name)
        model = MagicMock()
        model.invoke = AsyncMock(return_value=AssistantMessage(content="Primary compact state"))
        return model

    with patch("openjiuwen.core.context_engine.processor.forked.compressor.base.Model", side_effect=model_factory):
        compressor = DialogueCompressor(
            DialogueCompressorConfig(
                model=ModelRequestConfig(model_name="startup-model"),
                model_client=_client(),
                auto_compression={"model_name": "compact-primary", "fallback_model_name": "compact-backup"},
            )
        )
        event, _ = await compressor.on_get_context_window(
            _context(), _window(), model_config=ModelRequestConfig(model_name="desktop-selected"),
            model_client_config=_client(),
        )

    assert event is not None
    assert created == ["startup-model", "compact-primary"]


@pytest.mark.asyncio
async def test_auto_compression_does_not_fallback_on_authentication_failure():
    calls = []

    def model_factory(client, request):
        model = MagicMock()

        async def invoke(**kwargs):
            calls.append(request.model_name)
            raise RuntimeError("401 invalid api key")

        model.invoke = invoke
        return model

    with patch("openjiuwen.core.context_engine.processor.forked.compressor.base.Model", side_effect=model_factory):
        compressor = DialogueCompressor(
            DialogueCompressorConfig(
                model=ModelRequestConfig(model_name="startup-model"),
                model_client=_client(),
                auto_compression={"model_name": "compact-primary", "fallback_model_name": "compact-backup"},
            )
        )
        event, _ = await compressor.on_get_context_window(
            _context(), _window(), model_config=ModelRequestConfig(model_name="desktop-selected"),
            model_client_config=_client(),
        )

    assert event is None
    assert calls == ["compact-primary"]


def test_overflow_retry_budget_excludes_main_agent_system_and_tools():
    compressor = DialogueCompressor(DialogueCompressorConfig())
    messages = [
        UserMessage(content="old task"),
        AssistantMessage(content="older result"),
        AssistantMessage(content="another result"),
        UserMessage(content="current task"),
    ]
    window = ContextWindow(
        system_messages=[SystemMessage(content="system" * 100_000)],
        context_messages=messages,
        tools=[ToolInfo(name="huge_tool", description="tool" * 100_000, parameters={})],
    )
    compressor._resolve_context_max = MagicMock(return_value=10_000)

    retry_span = compressor._build_context_overflow_retry_span(
        context=_context(), context_window=window, span=compressor._build_span(messages),
        prompt="Summarize", budget_ratio=0.85,
    )

    assert retry_span is not None
    assert retry_span.has_target
    assert len(retry_span.protected_tail) > 1


def test_react_context_window_carries_the_selected_turn_model_to_passive_processors():
    agent = object.__new__(ReActAgent)
    selected = ModelRequestConfig(model_name="desktop-selected")
    client = _client()
    agent._config = MagicMock(model_config_obj=selected, model_client_config=client)
    ctx = MagicMock()
    ctx.inputs.tools = []

    kwargs = agent._build_context_window_kwargs(ctx, [SystemMessage(content="main system")])

    assert kwargs["model_config"] is selected
    assert kwargs["model_client_config"] is client
