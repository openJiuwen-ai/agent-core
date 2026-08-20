# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team registry boundary and idempotent close coverage for stateful workers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
    KVCacheRuntimeBinding,
    TeamKVCacheRegistry,
    TeamKVCState,
)
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.workflow.backends.avatar_session_backend import AvatarSessionManager
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig, KVCacheIdentity
from openjiuwen.core.session.agent import Session


class _ActionModel:
    def __init__(self, events: list[str], prefix: str, *, fail_evict: bool = False) -> None:
        self.events = events
        self.prefix = prefix
        self.fail_evict = fail_evict
        self.evict_calls: list[dict[str, Any]] = []
        self.offload_calls: list[dict[str, Any]] = []
        self.prefetch_calls: list[dict[str, Any]] = []

    def supports_kv_cache_affinity(self) -> bool:
        return True

    async def evict_kvc(self, **kwargs: Any) -> bool:
        self.events.append(f"{self.prefix}:evict")
        self.evict_calls.append(dict(kwargs))
        if self.fail_evict:
            raise RuntimeError("evict failed")
        return True

    async def offload_kvc(self, **kwargs: Any) -> bool:
        self.events.append(f"{self.prefix}:offload")
        self.offload_calls.append(dict(kwargs))
        return True

    async def prefetch_kvc(self, **kwargs: Any) -> bool:
        self.events.append(f"{self.prefix}:prefetch")
        self.prefetch_calls.append(dict(kwargs))
        return True


class _StatefulHarness:
    def __init__(self, events: list[str], model: _ActionModel) -> None:
        self.events = events
        self.model = model
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=True)
        )
        self.dispose_calls = 0
        self._on_state = None
        self._on_round = None
        self._round = 0

    def add_rail(self, _rail: Any) -> None:
        return None

    async def start(self, *, team_session: Any = None) -> None:
        self._session = Session()
        kv_cache_hooks.on_harness_session_created(self, self._session)
        self.events.append("stateful:start")

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
        self.events.append("stateful:send")
        self._round += 1
        if self._on_round is not None:
            await self._on_round(
                kind="finished",
                round_id=self._round,
                result={"output": f"reply:{content}", "result_type": "answer"},
            )
        if self._on_state is not None:
            await self._on_state(old=HarnessState.RUNNING, new=HarnessState.IDLE, session_id="stateful")
        return "seq"

    async def dispose(self) -> None:
        self.dispose_calls += 1
        self.events.append("stateful:dispose")


def _manager(
    monkeypatch: pytest.MonkeyPatch,
    harnesses: list[_StatefulHarness],
    events: list[str],
    model: _ActionModel,
) -> AvatarSessionManager:
    from openjiuwen.agent_teams.harness import team_harness as team_harness_module

    def _build(**_: Any) -> _StatefulHarness:
        harness = _StatefulHarness(events, model)
        harnesses.append(harness)
        return harness

    monkeypatch.setattr(team_harness_module.TeamHarness, "build", _build)
    base = DeepAgentSpec(
        tools=[],
        kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=True),
    )
    return AvatarSessionManager(
        worker_base_spec=base,
        team_name="team-a",
        session_id="team-session-a",
    )


@pytest.mark.asyncio
async def test_stateful_worker_stays_outside_team_registry_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    registry = TeamKVCacheRegistry()
    team_model = _ActionModel(events, "team")
    team_record = await registry.register_or_update(
        member_id="team-member-card",
        member_name="coder",
        binding=KVCacheRuntimeBinding(
            identity=KVCacheIdentity(
                cache_id=(
                    "team:team-session-a:team:team-a:member:team-member-card"
                ),
                parent_cache_id="team-session-a",
            ),
            model=team_model,
            enabled=True,
        ),
    )
    assert team_record is not None

    stateful_model = _ActionModel(events, "stateful")
    stateful_harnesses: list[_StatefulHarness] = []
    manager = _manager(monkeypatch, stateful_harnesses, events, stateful_model)
    session_id = await manager.open_session(kind="agent", instructions=None, opts={"label": "advisor"})
    await manager.send_turn(session_id, "hello", {"label": "advisor"}, None)

    snapshot = await registry.snapshot()
    assert snapshot == [team_record]
    assert stateful_harnesses[0].started_identity is not None
    assert stateful_harnesses[0].started_identity.cache_id not in {
        record.binding.identity.cache_id for record in snapshot
    }

    assert await registry.offload_all(reason="pause") == [True]
    assert team_record.state is TeamKVCState.OFFLOADED
    assert stateful_model.offload_calls == []
    assert stateful_model.evict_calls == []

    assert await registry.evict_all(reason="stop") == [True]
    assert team_record.state is TeamKVCState.EVICTED
    assert stateful_model.evict_calls == []

    await manager.close_session(session_id)

    assert events == [
        "stateful:start",
        "stateful:send",
        "team:offload",
        "team:evict",
        "stateful:evict",
        "stateful:dispose",
    ]
    assert len(stateful_model.evict_calls) == 1
    assert team_record.state is TeamKVCState.EVICTED
    assert await registry.snapshot() == [team_record]


@pytest.mark.asyncio
async def test_stateful_close_and_aclose_are_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    model = _ActionModel(events, "stateful")
    harnesses: list[_StatefulHarness] = []
    manager = _manager(monkeypatch, harnesses, events, model)

    session_id = await manager.open_session(kind="agent", instructions=None, opts={"label": "advisor"})
    await manager.send_turn(session_id, "hello", {"label": "advisor"}, None)
    await manager.close_session(session_id)
    await manager.close_session(session_id)

    assert len(model.evict_calls) == 1
    assert harnesses[0].dispose_calls == 1
    assert events == ["stateful:start", "stateful:send", "stateful:evict", "stateful:dispose"]

    events.clear()
    model2 = _ActionModel(events, "stateful")
    harnesses2: list[_StatefulHarness] = []
    manager2 = _manager(monkeypatch, harnesses2, events, model2)
    await manager2.open_session(kind="agent", instructions=None, opts={"label": "advisor"})
    await manager2.aclose()
    await manager2.aclose()

    assert len(model2.evict_calls) == 1
    assert harnesses2[0].dispose_calls == 1
    assert events == ["stateful:start", "stateful:evict", "stateful:dispose"]


@pytest.mark.asyncio
async def test_stateful_aclose_cleans_multiple_live_sessions_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    model = _ActionModel(events, "stateful")
    harnesses: list[_StatefulHarness] = []
    manager = _manager(monkeypatch, harnesses, events, model)

    await manager.open_session(kind="agent", instructions=None, opts={"label": "advisor-a"})
    await manager.open_session(kind="agent", instructions=None, opts={"label": "advisor-b"})
    await manager.aclose()

    assert [harness.dispose_calls for harness in harnesses] == [1, 1]
    assert len(model.evict_calls) == 2
    assert {call["session_id"] for call in model.evict_calls} == {
        harness.started_identity.cache_id for harness in harnesses if harness.started_identity is not None
    }
    assert events == [
        "stateful:start", "stateful:start", "stateful:evict", "stateful:dispose",
        "stateful:evict", "stateful:dispose",
    ]


@pytest.mark.asyncio
async def test_stateful_close_evict_failure_still_disposes(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    model = _ActionModel(events, "stateful", fail_evict=True)
    harnesses: list[_StatefulHarness] = []
    manager = _manager(monkeypatch, harnesses, events, model)
    session_id = await manager.open_session(kind="agent", instructions=None, opts={"label": "advisor"})

    await manager.close_session(session_id)

    assert len(model.evict_calls) == 1
    assert harnesses[0].dispose_calls == 1
    assert events == ["stateful:start", "stateful:evict", "stateful:dispose"]
