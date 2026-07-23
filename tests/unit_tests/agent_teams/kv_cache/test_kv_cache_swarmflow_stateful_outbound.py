# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Final outbound payload coverage for stateful swarmflow workers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import AvatarSessionManager
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.foundation.kv_cache import (
    KVC_SESSION_EVICT_TIMEOUT_SECONDS,
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


class _CapturingModel:
    def __init__(self, *, supports: bool, events: list[str]) -> None:
        self._supports = supports
        self.events = events
        self.payloads: list[dict[str, Any]] = []
        self.evict_calls: list[dict[str, Any]] = []
        self.offload_calls: list[dict[str, Any]] = []
        self.prefetch_calls: list[dict[str, Any]] = []
        self.client = AscendAffinityModelClient(
            model_config=ModelRequestConfig(model="test-model"),
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.AscendAffinity,
                api_key="test-key",
                api_base="https://example.test",
                verify_ssl=False,
            ),
        )

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
        self.events.append("evict")
        self.evict_calls.append(dict(kwargs))
        return True

    async def offload_kvc(self, **kwargs: Any) -> bool:
        self.events.append("offload")
        self.offload_calls.append(dict(kwargs))
        return True

    async def prefetch_kvc(self, **kwargs: Any) -> bool:
        self.events.append("prefetch")
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


async def _capture_payload_call(
    *,
    enabled: bool,
    model: _CapturingModel,
    identity: KVCacheIdentity | None,
) -> None:
    agent = ReActAgent(card=AgentCard(id="stateful", name="stateful"))
    config = ReActAgentConfig()
    config.model_name = "test-model"
    config.kv_cache_affinity_config = KVCacheAffinityConfig(enable_kv_cache_affinity=enabled)
    agent.configure(config)
    agent.set_llm(model)

    envs = {}
    if identity is not None:
        envs = {
            "kv_cache_affinity_session_id": identity.cache_id,
            "kv_cache_affinity_parent_session_id": identity.parent_cache_id,
        }
    session = Session(session_id=identity.cache_id if identity else "plain-stateful", envs=envs)
    ctx = AgentCallbackContext(
        agent=agent,
        session=session,
        context=_Context(),
        inputs=ModelCallInputs(messages=[], tools=[]),
        extra={},
    )
    await agent._railed_model_call(ctx)


class _PayloadHarness:
    def __init__(self, *, enabled: bool, supports: bool, events: list[str]) -> None:
        self.events = events
        self.model = _CapturingModel(supports=supports, events=events)
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled)
        )
        self.sends: list[str] = []
        self.disposes = 0
        self._on_state = None
        self._on_round = None
        self._round = 0

    def add_rail(self, _rail: Any) -> None:
        return None

    async def start(self, *, team_session: Any = None) -> None:
        self._session = Session()
        kv_cache_hooks.on_harness_session_created(self, self._session)
        self.events.append("start")

    def current_session(self) -> Session | None:
        return getattr(self, "_session", None)

    @property
    def started_identity(self) -> KVCacheIdentity | None:
        session = self.current_session()
        return session.get_cache_identity() if session is not None else None

    async def subscribe(self, *, on_state=None, on_round=None) -> None:
        self._on_state = on_state
        self._on_round = on_round

    async def send(self, content: str, *, immediate: bool = False) -> str:
        self.events.append(f"send:{content}")
        self.sends.append(content)
        await _capture_payload_call(
            enabled=self.deep_config.kv_cache_affinity_config.enable_kv_cache_affinity,
            model=self.model,
            identity=self.started_identity,
        )
        self._round += 1
        if self._on_round is not None:
            await self._on_round(
                kind="finished",
                round_id=self._round,
                result={"output": f"echo:{content}", "result_type": "answer"},
            )
        if self._on_state is not None:
            await self._on_state(old=HarnessState.RUNNING, new=HarnessState.IDLE, session_id="stateful")
        return "seq"

    async def dispose(self) -> None:
        self.disposes += 1
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
async def test_stateful_worker_two_turns_final_outbound_payload(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    supports: bool,
    expect_hint: bool,
) -> None:
    from openjiuwen.agent_teams.harness import team_harness as team_harness_module

    events: list[str] = []
    harnesses: list[_PayloadHarness] = []

    def _build(**_: Any) -> _PayloadHarness:
        harness = _PayloadHarness(enabled=enabled, supports=supports, events=events)
        harnesses.append(harness)
        return harness

    monkeypatch.setattr(team_harness_module.TeamHarness, "build", _build)
    base = DeepAgentSpec(
        tools=[],
        kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled),
    )
    manager = AvatarSessionManager(
        worker_base_spec=base,
        team_name="team-a",
        session_id="team-session-a",
    )

    session_id = await manager.open_session(kind="agent", instructions=None, opts={"label": "advisor"})
    await manager.send_turn(session_id, "first", {"label": "advisor"}, None)
    await manager.send_turn(session_id, "second", {"label": "advisor"}, None)

    assert len(harnesses) == 1
    harness = harnesses[0]
    assert harness.sends == ["first", "second"]
    assert len(harness.model.payloads) == 2
    assert harness.model.offload_calls == []
    assert harness.model.prefetch_calls == []
    assert harness.model.evict_calls == []
    assert harness.disposes == 0

    if expect_hint:
        identity = harness.started_identity
        assert identity is not None
        hints = [payload["agent_hint"] for payload in harness.model.payloads]
        assert hints == [
            {"session_id": identity.cache_id, "parent_session_id": "team-session-a"},
            {"session_id": identity.cache_id, "parent_session_id": "team-session-a"},
        ]
    else:
        assert all("agent_hint" not in payload for payload in harness.model.payloads)

    await manager.close_session(session_id)

    if expect_hint:
        identity = harness.started_identity
        assert identity is not None
        assert harness.model.evict_calls == [
            {
                "target": "session",
                "session_id": identity.cache_id,
                "parent_session_id": "team-session-a",
                "timeout": KVC_SESSION_EVICT_TIMEOUT_SECONDS,
            }
        ]
    else:
        assert harness.model.evict_calls == []
    assert harness.disposes == 1
