# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Outbound payload gate tests for Team and Swarmflow KVC affinity."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
    TeamKVCacheRegistry,
    register_harness_binding,
)
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.foundation.kv_cache import (
    KVCacheAffinityConfig,
    KVCacheIdentity,
)
from openjiuwen.core.foundation.llm.model_clients.ascend_affinity_model_client import (
    AscendAffinityModelClient,
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, SystemMessage, UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _client() -> AscendAffinityModelClient:
    return AscendAffinityModelClient(
        model_config=ModelRequestConfig(model="test-model"),
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.AscendAffinity,
            api_key="test-key",
            api_base="https://example.test",
            verify_ssl=False,
        ),
    )


class _CapturingAffinityModel:
    def __init__(self, *, supports: bool) -> None:
        self._supports = supports
        self.client = _client()
        self.payloads: list[dict[str, Any]] = []
        self.evict_calls: list[dict[str, Any]] = []
        self.offload_calls: list[dict[str, Any]] = []
        self.prefetch_calls: list[dict[str, Any]] = []

    def supports_kv_cache_release(self) -> bool:
        return False

    def supports_kv_cache_affinity(self) -> bool:
        return self._supports

    def build_kv_cache_invoke_kwargs(self, **_: Any) -> dict[str, Any]:
        return {}

    def build_kv_cache_affinity_invoke_kwargs(self, **kwargs: Any) -> dict[str, Any]:
        if not self._supports:
            return {}
        return self.client.build_kv_cache_affinity_invoke_kwargs(**kwargs)

    async def invoke(self, *, model: str, messages: list[Any], tools: Any = None, **kwargs: Any) -> AssistantMessage:
        payload = self.client._build_request_params(
            messages=messages,
            tools=tools,
            temperature=None,
            top_p=None,
            model=model,
            stop=None,
            max_tokens=None,
            stream=False,
            **kwargs,
        )
        self.payloads.append(payload)
        return AssistantMessage(content="ok")

    async def evict_kvc(self, **kwargs: Any) -> bool:
        self.evict_calls.append(dict(kwargs))
        return True

    async def offload_kvc(self, **kwargs: Any) -> bool:
        self.offload_calls.append(dict(kwargs))
        return True

    async def prefetch_kvc(self, **kwargs: Any) -> bool:
        self.prefetch_calls.append(dict(kwargs))
        return True


class _Context:
    async def get_context_window(self, **_: Any) -> ContextWindow:
        return ContextWindow(
            system_messages=[SystemMessage(content="system")],
            context_messages=[UserMessage(content="hello")],
            tools=[],
        )

    def detect_context_window_change(self, window: ContextWindow) -> None:
        return None

    def session_id(self) -> str:
        return "fallback-session"


def _agent(*, enabled: bool, model: _CapturingAffinityModel) -> ReActAgent:
    agent = ReActAgent(card=AgentCard(id="agent", name="agent"))
    config = ReActAgentConfig()
    config.model_name = "test-model"
    config.kv_cache_affinity_config = KVCacheAffinityConfig(enable_kv_cache_affinity=enabled)
    agent.configure(config)
    agent.set_llm(model)
    return agent


async def _run_react_call(
    *,
    enabled: bool,
    supports: bool,
    session_id: str,
    parent_session_id: str,
) -> tuple[AssistantMessage, _CapturingAffinityModel]:
    model = _CapturingAffinityModel(supports=supports)
    agent = _agent(enabled=enabled, model=model)
    session = Session(
        session_id=session_id,
        envs={"kv_cache_affinity_parent_session_id": parent_session_id},
    )
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        context=_Context(),
        inputs=ModelCallInputs(messages=[], tools=[]),
        extra={},
    )
    return await agent._railed_model_call(ctx), model


class _HarnessForRegistry:
    def __init__(self, *, enabled: bool, supports: bool, identity: KVCacheIdentity) -> None:
        self.model = _CapturingAffinityModel(supports=supports)
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled)
        )
        self._identity = identity

    def current_kv_cache_identity(self) -> KVCacheIdentity:
        return self._identity


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["leader", "teammate"])
@pytest.mark.parametrize(
    ("enabled", "supports", "expect_hint"),
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
async def test_team_member_outbound_payload_gate_and_registry_noop(role: str, enabled: bool, supports: bool, expect_hint: bool) -> None:
    member_id = "leader-card" if role == "leader" else "teammate-card"
    cache_id = f"team:team-sid:team:team-a:member:{member_id}"

    result, model = await _run_react_call(
        enabled=enabled,
        supports=supports,
        session_id=cache_id,
        parent_session_id="team-sid",
    )

    assert result.content == "ok"
    assert len(model.payloads) == 1
    payload = model.payloads[0]
    if expect_hint:
        assert payload["agent_hint"] == {
            "session_id": cache_id,
            "parent_session_id": "team-sid",
        }
    else:
        assert "agent_hint" not in payload

    registry = TeamKVCacheRegistry()
    harness = _HarnessForRegistry(
        enabled=enabled,
        supports=supports,
        identity=KVCacheIdentity(cache_id=cache_id, parent_cache_id="team-sid"),
    )
    record = await register_harness_binding(
        registry,
        member_id=member_id,
        member_name=role,
        harness=harness,
    )
    if expect_hint:
        assert record is not None
    else:
        assert record is None
        assert await registry.snapshot() == []

    assert model.evict_calls == []
    assert model.offload_calls == []
    assert model.prefetch_calls == []


class _WorkerPayloadHarness:
    def __init__(self, *, enabled: bool, supports: bool, events: list[str]) -> None:
        self.model = _CapturingAffinityModel(supports=supports)
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled)
        )
        self.events = events
        self.identity: KVCacheIdentity | None = None

    def add_rail(self, rail: Any) -> None:
        return None

    async def run_once(self, content: Any, **_: Any) -> dict[str, Any]:
        self.events.append("inference")
        session = Session()
        kv_cache_hooks.on_harness_session_created(self, session)
        self.identity = session.get_cache_identity()
        try:
            result, model = await _run_react_call(
                enabled=self.deep_config.kv_cache_affinity_config.enable_kv_cache_affinity,
                supports=self.model.supports_kv_cache_affinity(),
                session_id=self.identity.cache_id,
                parent_session_id=self.identity.parent_cache_id,
            )
            self.model.payloads.extend(model.payloads)
            return {"output": result.content}
        finally:
            await kv_cache_hooks.after_harness_session_finished(self, session)

    async def dispose(self) -> None:
        self.events.append("dispose")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "supports", "expect_hint"),
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
async def test_swarmflow_worker_outbound_payload_gate_and_cleanup_noop(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    supports: bool,
    expect_hint: bool,
) -> None:
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    events: list[str] = []
    built: list[_WorkerPayloadHarness] = []

    def _fake_build(**_: Any) -> _WorkerPayloadHarness:
        harness = _WorkerPayloadHarness(enabled=enabled, supports=supports, events=events)
        built.append(harness)
        return harness

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)

    backend = TeamWorkerBackend(
        model=None,
        worker_base_spec=DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[]),
        team_name="team-a",
        session_id="sess-a",
        run_id="run-a",
    )

    text = await backend._execute_worker("hello", [], member_name="wf-worker-0", has_schema=False, model=None)

    assert text == "ok"
    assert events[0] == "inference"
    assert built
    payload = built[0].model.payloads[0]
    if expect_hint:
        identity = built[0].identity
        assert identity is not None
        assert payload["agent_hint"] == {
            "session_id": identity.cache_id,
            "parent_session_id": identity.parent_cache_id,
        }
        assert events[-1] == "dispose"
    else:
        assert "agent_hint" not in payload
        assert events == ["inference", "dispose"]

    assert built[0].model.offload_calls == []
    assert built[0].model.prefetch_calls == []
