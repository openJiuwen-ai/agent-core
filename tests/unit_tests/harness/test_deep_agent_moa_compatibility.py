# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model reload and native lifecycle compatibility with optional MoA integration."""

from __future__ import annotations

import asyncio
import builtins
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig, UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import DeepAgent
from openjiuwen.harness.moa import MoAConfig
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.config import DeepAgentConfig


@pytest.fixture
def _offline_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Model, "invoke", AsyncMock(side_effect=AssertionError("Unexpected model request")))
    monkeypatch.setattr("openjiuwen.harness.deep_agent.schedule_image_support_probe", MagicMock())


def _model(
    name: str = "reload-test-model",
    *,
    provider: str = "OpenAI",
    endpoint: str = "https://example.test/v1",
    context_window: int | None = None,
) -> Model:
    return Model(
        model_client_config=ModelClientConfig(client_provider=provider, api_key="test-key", api_base=endpoint),
        model_config=ModelRequestConfig(model=name, context_window=context_window),
    )


class _BrowserPolicyModel(Model):
    """Minimal browser policy with the same fallback rebinding contract."""

    def __init__(self, fallback: Model) -> None:
        self.bind_fallback(fallback)

    def bind_fallback(self, model: Model) -> None:
        self.fallback = model
        self.model_config = model.model_config
        self.model_client_config = model.model_client_config
        self._client = model._client


def _agent(model: Model, *, moa_enabled: bool = False) -> tuple[DeepAgent, DeepAgentConfig]:
    config = DeepAgentConfig(
        model=model,
        moa=MoAConfig(reference_models=(_model(),), mode="verifier_routed", scorer=lambda **_: 1.0)
        if moa_enabled
        else None,
        system_prompt="Original prompt",
        enable_task_loop=False,
    )
    agent = DeepAgent(AgentCard(name="reload-test")).configure(config)
    assert isinstance(agent.react_agent, ReActAgent)
    # Complete an initial reload so legacy direct provider fields are populated.
    # The subsequent reloads exercise an already-running, explicitly bound model.
    agent.configure(replace(config))
    agent.react_agent.set_llm(model)
    return agent, config


@pytest.mark.level0
@pytest.mark.usefixtures("_offline_models")
def test_prompt_reload_preserves_dynamically_selected_model_without_moa() -> None:
    configured_model = _model()
    agent, config = _agent(configured_model)
    selected_model = _model("selected-model")
    agent.react_agent.set_llm(selected_model)

    agent.configure(replace(config, system_prompt="Updated prompt"))

    assert agent.react_agent._get_llm() is selected_model
    assert agent.system_prompt_builder.build() == "Updated prompt"


@pytest.mark.level1
@pytest.mark.parametrize("change", ["endpoint", "provider"])
@pytest.mark.usefixtures("_offline_models")
def test_provider_config_changes_rebuild_model_lazily_without_moa(change: str) -> None:
    original = _model()
    agent, config = _agent(original)
    replacement = _model(
        provider="Anthropic" if change == "provider" else "OpenAI",
        endpoint="https://other.example.test/v1" if change == "endpoint" else "https://example.test/v1",
    )

    agent.configure(replace(config, model=replacement))

    assert agent.react_agent._llm is None
    rebuilt = agent.react_agent._get_llm()
    assert rebuilt is not original
    assert rebuilt is not replacement
    assert rebuilt.model_client_config == replacement.model_client_config
    assert rebuilt.model_config == replacement.model_config
    assert agent.react_agent._get_llm() is rebuilt


@pytest.mark.level0
@pytest.mark.parametrize("moa_enabled", [False, True], ids=["native", "moa"])
@pytest.mark.parametrize("browser_policy", [False, True], ids=["ordinary", "browser"])
@pytest.mark.usefixtures("_offline_models")
def test_reload_retains_policy_and_replaces_explicit_model_only_for_moa(
    moa_enabled: bool, browser_policy: bool
) -> None:
    original = _model()
    active = _BrowserPolicyModel(original) if browser_policy else original
    agent, config = _agent(active, moa_enabled=moa_enabled)
    replacement = _model()

    agent.configure(replace(config, model=replacement, system_prompt="Updated prompt"))

    if browser_policy:
        assert agent.react_agent._get_llm() is active
        assert active.fallback is replacement
    else:
        assert agent.react_agent._get_llm() is (replacement if moa_enabled else original)


@pytest.mark.level1
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("moa_enabled", "browser_policy"),
    [(False, False), (True, False), (True, True)],
    ids=["native", "moa", "moa-browser"],
)
@pytest.mark.parametrize("model_window", [None, 131072], ids=["no-metadata", "model-metadata"])
@pytest.mark.parametrize("global_window", [None, 32768], ids=["model-window", "global-window"])
@pytest.mark.usefixtures("_offline_models")
async def test_reload_preserves_resolved_window_in_config_engine_and_cached_context(
    moa_enabled: bool, browser_policy: bool, model_window: int | None, global_window: int | None
) -> None:
    original = _model(context_window=model_window)
    active = _BrowserPolicyModel(original) if browser_policy else original
    agent, config = _agent(active, moa_enabled=moa_enabled)
    react = agent.react_agent
    context_config = ContextEngineConfig(
        model_name=original.model_config.model_name,
        model_provider="OpenAI",
        context_window_tokens=global_window,
        model_context_window_tokens_override=65536,
    )
    # Prepare the same resolved context settings as configure() will produce.
    # This keeps an existing session cached through the reload, so checking only
    # the new config cannot hide a stale live context.
    react.configure(react.config.model_copy(update={"context_engine_config": context_config}))
    resolved_window = model_window or 65536
    assert react.config.context_engine_config.model_context_window_tokens_override == resolved_window
    engine = react.context_engine
    session = Session(session_id="reload-window")
    context = await engine.create_context(session=session)
    await context.add_messages(UserMessage(content="Retain this conversation"))
    replacement = _model(context_window=model_window)

    agent.configure(replace(config, model=replacement, context_engine_config=context_config))

    assert react.context_engine is engine
    assert engine.get_context(session_id=session.get_session_id()) is context
    assert react.config.context_engine_config.model_context_window_tokens_override == resolved_window
    assert engine._config.model_context_window_tokens_override == resolved_window
    assert react.config.context_engine_config.context_window_tokens == global_window
    assert engine._config.context_window_tokens == global_window
    assert context.context_window_tokens() == (global_window or resolved_window)
    assert [(message.role, message.content) for message in context.get_messages()] == [
        ("user", "Retain this conversation")
    ]
    assert agent._resolve_context_window_tokens() == (global_window or resolved_window)


class _LifecycleRail(DeepAgentRail):
    def __init__(self, name: str, events: list[str], *, priority: int = 100) -> None:
        super().__init__()
        self.name = name
        self.events = events
        self.priority = priority
        self.init_error: BaseException | None = None

    def init(self, agent: DeepAgent) -> None:
        assert agent.is_pending_rail(self)
        self.events.append(f"init:{self.name}")
        if self.init_error is not None:
            raise self.init_error

    def uninit(self, agent: DeepAgent) -> None:
        assert not agent.is_pending_rail(self)
        assert not agent.is_registered_rail(self)
        self.events.append(f"uninit:{self.name}")

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        assert isinstance(ctx.agent, DeepAgent)
        self.events.append(f"outer:{self.name}")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        assert not isinstance(ctx.agent, DeepAgent)
        self.events.append(f"inner:{self.name}")


@pytest_asyncio.fixture
async def native_factory(monkeypatch):
    # Environment-driven optional training rails are unrelated to this contract.
    monkeypatch.setattr(DeepAgent, "_build_online_training_rail_from_env", lambda _: None)
    agents = []

    def build(rails: list[_LifecycleRail]) -> DeepAgent:
        card = AgentCard(id=f"native-lifecycle-{uuid4().hex}", name="native-lifecycle")
        agent = DeepAgent(card).configure(
            DeepAgentConfig(
                card=card,
                rails=rails,
                auto_create_workspace=False,
                enable_read_image_multimodal=False,
            )
        )
        assert agent.deep_config.moa is None
        agents.append(agent)
        return agent

    yield build
    for agent in agents:
        await agent.agent_callback_manager.clear()
        await agent.react_agent.agent_callback_manager.clear()


async def _fire_callbacks(agent: DeepAgent) -> None:
    await AgentCallbackContext(agent=agent).fire(AgentCallbackEvent.BEFORE_INVOKE)
    await AgentCallbackContext(agent=agent.react_agent).fire(AgentCallbackEvent.BEFORE_MODEL_CALL)


def _trace_retirement(monkeypatch, agent: DeepAgent, events: list[str]) -> None:
    outer = agent.agent_callback_manager.unregister_rail
    inner = agent.react_agent.agent_callback_manager.unregister_rail

    async def unregister_outer(rail, owner):
        events.append(f"remove-outer:{rail.name}")
        await outer(rail, owner)

    async def unregister_inner(rail, owner):
        events.append(f"remove-inner:{rail.name}")
        await inner(rail, owner)

    monkeypatch.setattr(agent.agent_callback_manager, "unregister_rail", unregister_outer)
    monkeypatch.setattr(agent.react_agent.agent_callback_manager, "unregister_rail", unregister_inner)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_native_initialization_keeps_stable_priority_order_and_is_idempotent(native_factory, monkeypatch):
    events = []
    low = _LifecycleRail("low", events, priority=10)
    first = _LifecycleRail("first", events)
    second = _LifecycleRail("second", events)
    agent = native_factory([low, first, second])

    async def register_mcps():
        events.append("mcps")

    async def init_workspace():
        events.append("workspace")

    async def resolve_multimodal():
        events.append("multimodal")

    monkeypatch.setattr(agent, "_register_pending_mcps", register_mcps)
    monkeypatch.setattr(agent, "_needs_workspace_init", lambda: True)
    monkeypatch.setattr(agent, "init_workspace", init_workspace)
    monkeypatch.setattr(agent, "_resolve_read_image_multimodal", resolve_multimodal)

    assert not agent.is_initialized
    assert all(agent.is_pending_rail(rail) for rail in (low, first, second))
    await agent.ensure_initialized()
    assert events == ["mcps", "workspace", "multimodal", "init:first", "init:second", "init:low"]
    assert agent.is_initialized
    assert agent._pending_rails == []
    assert agent._registered_rails == [first, second, low]
    assert not agent.agent_callback_manager.has_hooks(AgentCallbackEvent.BEFORE_MODEL_CALL)
    assert not agent.react_agent.agent_callback_manager.has_hooks(AgentCallbackEvent.BEFORE_INVOKE)

    events.clear()
    await agent.ensure_initialized()
    assert events == []
    await _fire_callbacks(agent)
    assert events == ["outer:first", "outer:second", "outer:low", "inner:first", "inner:second", "inner:low"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_native_reconfigure_retires_stale_rails_before_initializing_replacements(native_factory, monkeypatch):
    events = []
    old = _LifecycleRail("old", events)
    other = _LifecycleRail("other", events)
    replacement = _LifecycleRail("replacement", events)
    agent = native_factory([old, other])
    await agent.ensure_initialized()
    _trace_retirement(monkeypatch, agent, events)

    agent.configure(replace(agent.deep_config, rails=[replacement]))
    assert not agent.is_initialized
    assert agent._stale_rails == [old, other]
    events.clear()
    await agent.ensure_initialized()

    assert events == [
        "remove-outer:old",
        "remove-inner:old",
        "uninit:old",
        "remove-outer:other",
        "remove-inner:other",
        "uninit:other",
        "init:replacement",
    ]
    assert agent._stale_rails == agent._pending_rails == []
    assert agent._registered_rails == [replacement]
    assert agent.is_initialized
    events.clear()
    await _fire_callbacks(agent)
    assert events == ["outer:replacement", "inner:replacement"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_native_reconfigure_reuses_the_same_rail_without_retiring_its_resources(native_factory, monkeypatch):
    events = []
    retained = _LifecycleRail("retained", events)
    agent = native_factory([retained])
    await agent.ensure_initialized()
    _trace_retirement(monkeypatch, agent, events)

    agent.configure(replace(agent.deep_config, rails=[retained]))
    assert agent._stale_rails == agent._pending_rails == [retained]
    events.clear()
    await agent.ensure_initialized()

    assert events == ["init:retained"]
    assert agent._stale_rails == agent._pending_rails == []
    assert agent._registered_rails == [retained]
    assert agent.is_initialized


@pytest.mark.asyncio
@pytest.mark.level1
async def test_native_unregister_avoids_moa_and_removes_callbacks_before_uninit(native_factory, monkeypatch):
    events = []
    rail = _LifecycleRail("ordinary", events)
    agent = native_factory([rail])
    original_import = builtins.__import__

    def import_without_moa(name, *args, **kwargs):
        assert name != "openjiuwen.harness.moa.integration", "Plain rail lifecycle must not import MoAIntegration"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_moa)
    await agent.ensure_initialized()
    agent.add_rail(rail)
    _trace_retirement(monkeypatch, agent, events)
    events.clear()
    assert await agent.unregister_rail(rail) is agent
    assert events == ["remove-outer:ordinary", "remove-inner:ordinary", "uninit:ordinary"]
    assert agent._pending_rails == agent._registered_rails == []
    assert agent.is_initialized
    events.clear()
    await _fire_callbacks(agent)
    assert events == []


@pytest.mark.asyncio
@pytest.mark.level1
@pytest.mark.parametrize("failure_stage", ["init", "registration"])
async def test_native_initialization_failure_preserves_original_error_and_partial_state(
    native_factory, monkeypatch, failure_stage
):
    events = []
    ready = _LifecycleRail("ready", events, priority=200)
    failing = _LifecycleRail("failing", events)
    later = _LifecycleRail("later", events, priority=10)
    agent = native_factory([later, failing, ready])
    error = RuntimeError(f"native {failure_stage} failed")
    if failure_stage == "init":
        failing.init_error = error
    else:
        register_callback = agent.react_agent.register_callback

        async def fail_registration(event, callback, priority):
            if callback.__self__ is failing:
                raise error
            await register_callback(event, callback, priority)

        monkeypatch.setattr(agent.react_agent, "register_callback", fail_registration)

    with pytest.raises(RuntimeError) as caught:
        await agent.ensure_initialized()

    assert caught.value is error
    assert not agent.is_initialized
    assert agent._pending_rails == [later, failing, ready]
    assert agent._registered_rails == [ready]
    assert events == ["init:ready", "init:failing"]
    events.clear()
    await _fire_callbacks(agent)
    expected = ["outer:ready"]
    if failure_stage == "registration":
        # Native registration retains callbacks registered before a later failure.
        expected.append("outer:failing")
    assert events == [*expected, "inner:ready"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_native_initialization_cancellation_preserves_registered_rails(native_factory, monkeypatch):
    events = []
    ready = _LifecycleRail("ready", events, priority=200)
    pending = _LifecycleRail("pending", events)
    agent = native_factory([pending, ready])
    registering = asyncio.Event()
    register_callback = agent.react_agent.register_callback

    async def wait_during_registration(event, callback, priority):
        if callback.__self__ is pending:
            registering.set()
            await asyncio.Event().wait()
        await register_callback(event, callback, priority)

    monkeypatch.setattr(agent.react_agent, "register_callback", wait_during_registration)
    task = asyncio.create_task(agent.ensure_initialized())
    try:
        await asyncio.wait_for(registering.wait(), timeout=2)
        task.cancel("native initialization cancelled")
        with pytest.raises(asyncio.CancelledError, match="native initialization cancelled"):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert not agent.is_initialized
    assert agent._pending_rails == [pending, ready]
    assert agent._registered_rails == [ready]
    assert events == ["init:ready", "init:pending"]
    events.clear()
    await _fire_callbacks(agent)
    assert events == ["outer:ready", "outer:pending", "inner:ready"]
