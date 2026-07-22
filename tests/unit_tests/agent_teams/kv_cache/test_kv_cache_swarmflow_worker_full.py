# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Full TeamWorkerBackend KVC cleanup tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.kv_cache import kv_cache_cleanup as cleanup_module
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig, KVCacheIdentity
from openjiuwen.core.session.agent import create_agent_session


class _WorkerModel:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.evict_identities: list[tuple[str, str | None]] = []

    def supports_kv_cache_affinity(self) -> bool:
        return True

    async def evict_kvc(self, **kwargs: Any) -> bool:
        self.events.append("evict")
        self.evict_identities.append((kwargs["session_id"], kwargs.get("parent_session_id")))
        return True

    async def offload_kvc(self, **kwargs: Any) -> bool:
        raise AssertionError("worker must not offload")

    async def prefetch_kvc(self, **kwargs: Any) -> bool:
        raise AssertionError("new worker must not prefetch")


class _FullWorkerHarness:
    def __init__(self, events: list[str], *, outcome: str, block_cancel: asyncio.Event | None = None) -> None:
        self.events = events
        self.outcome = outcome
        self.block_cancel = block_cancel
        self.model = _WorkerModel(events)
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=True)
        )
        self.identities: list[KVCacheIdentity] = []

    def add_rail(self, rail: Any) -> None:
        return None

    async def run_once(self, content: Any, **_: Any) -> dict[str, Any]:
        session = create_agent_session()
        kv_cache_hooks.on_harness_session_created(self, session)
        self.identities.append(session.get_cache_identity())
        self.events.append("inference")
        try:
            if self.outcome == "failure":
                raise ValueError("business failed")
            if self.outcome == "cancel":
                assert self.block_cancel is not None
                await self.block_cancel.wait()
            return {"output": "ok"}
        finally:
            await kv_cache_hooks.after_harness_session_finished(self, session)

    async def dispose(self) -> None:
        self.events.append("dispose")


def _backend(monkeypatch: pytest.MonkeyPatch, harnesses: list[_FullWorkerHarness], *, outcome: str, block_cancel: asyncio.Event | None = None) -> TeamWorkerBackend:
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    def _fake_build(**_: Any) -> _FullWorkerHarness:
        harness = _FullWorkerHarness([], outcome=outcome, block_cancel=block_cancel)
        harnesses.append(harness)
        return harness

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)
    return TeamWorkerBackend(
        model=None,
        worker_base_spec=DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[]),
        team_name="team-a",
        session_id="sess-a",
        run_id="run-a",
    )


@pytest.mark.asyncio
async def test_execute_worker_success_runs_inference_evict_dispose(monkeypatch: pytest.MonkeyPatch) -> None:
    harnesses: list[_FullWorkerHarness] = []
    backend = _backend(monkeypatch, harnesses, outcome="success")

    result = await backend._execute_worker("prompt", [], member_name="wf-worker-0", has_schema=False, model=None)

    assert result == "ok"
    assert harnesses[0].events == ["inference", "evict", "dispose"]
    identity = harnesses[0].identities[0]
    assert harnesses[0].model.evict_identities == [
        (identity.cache_id, identity.parent_cache_id)
    ]


@pytest.mark.asyncio
async def test_execute_worker_failure_runs_cleanup_and_preserves_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    harnesses: list[_FullWorkerHarness] = []
    backend = _backend(monkeypatch, harnesses, outcome="failure")

    with pytest.raises(BackendError) as exc_info:
        await backend._execute_worker("prompt", [], member_name="wf-worker-0", has_schema=False, model=None)

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert str(exc_info.value.__cause__) == "business failed"
    assert harnesses[0].events == ["inference", "evict", "dispose"]


@pytest.mark.asyncio
async def test_execute_worker_cancel_runs_cleanup_and_reraises_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    unblock = asyncio.Event()
    harnesses: list[_FullWorkerHarness] = []
    backend = _backend(monkeypatch, harnesses, outcome="cancel", block_cancel=unblock)

    task = asyncio.create_task(
        backend._execute_worker("prompt", [], member_name="wf-worker-0", has_schema=False, model=None)
    )
    while not harnesses or harnesses[0].events != ["inference"]:
        await asyncio.sleep(0)
    task.cancel()
    unblock.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert harnesses[0].events == ["inference", "evict", "dispose"]


@pytest.mark.asyncio
async def test_execute_worker_parallel_identities_do_not_collide(monkeypatch: pytest.MonkeyPatch) -> None:
    harnesses: list[_FullWorkerHarness] = []
    backend = _backend(monkeypatch, harnesses, outcome="success")

    await asyncio.gather(
        backend._execute_worker("prompt-a", [], member_name="wf-worker-0", has_schema=False, model=None),
        backend._execute_worker("prompt-b", [], member_name="wf-worker-1", has_schema=False, model=None),
    )

    identities = {h.identities[0].cache_id for h in harnesses}
    assert len(identities) == 2
    assert all(
        identity.parent_cache_id == "sess-a"
        for harness in harnesses
        for identity in harness.identities
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "supports"),
    [
        (False, True),
        (True, False),
    ],
)
async def test_execute_worker_unmanageable_binding_does_not_pass_identity(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    supports: bool,
) -> None:
    harnesses: list[_FullWorkerHarness] = []

    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    def _fake_build(**_: Any) -> _FullWorkerHarness:
        harness = _FullWorkerHarness([], outcome="success")
        harness.deep_config.kv_cache_affinity_config = KVCacheAffinityConfig(
            enable_kv_cache_affinity=enabled
        )
        harness.model.supports_kv_cache_affinity = lambda: supports
        harnesses.append(harness)
        return harness

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)
    backend = TeamWorkerBackend(
        model=None,
        worker_base_spec=DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[]),
        team_name="team-a",
        session_id="sess-a",
        run_id="run-a",
    )

    result = await backend._execute_worker("prompt", [], member_name="wf-worker-0", has_schema=False, model=None)

    assert result == "ok"
    assert len(harnesses[0].identities) == 1
    assert harnesses[0].model.evict_identities == []
    assert harnesses[0].events == ["inference", "dispose"]


@pytest.mark.asyncio
async def test_execute_worker_capability_check_failure_does_not_pass_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harnesses: list[_FullWorkerHarness] = []

    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    def _fake_build(**_: Any) -> _FullWorkerHarness:
        harness = _FullWorkerHarness([], outcome="success")

        def _raise_supports() -> bool:
            raise RuntimeError("capability check failed")

        harness.model.supports_kv_cache_affinity = _raise_supports
        harnesses.append(harness)
        return harness

    monkeypatch.setattr(th_mod.TeamHarness, "build", _fake_build)
    backend = TeamWorkerBackend(
        model=None,
        worker_base_spec=DeepAgentSpec(enable_task_loop=True, enable_task_planning=True, tools=[]),
        team_name="team-a",
        session_id="sess-a",
        run_id="run-a",
    )

    result = await backend._execute_worker("prompt", [], member_name="wf-worker-0", has_schema=False, model=None)

    assert result == "ok"
    assert len(harnesses[0].identities) == 1
    assert harnesses[0].model.evict_identities == []
    assert harnesses[0].events == ["inference", "dispose"]


@pytest.mark.asyncio
async def test_worker_cleanup_runs_evict_before_dispose(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def _evict(binding: object, *, reason: str, worker_id: str) -> bool:
        events.append(f"evict:{reason}:{worker_id}")
        return True

    async def _dispose() -> None:
        events.append("dispose")

    monkeypatch.setattr(cleanup_module, "cancellation_safe_best_effort_evict", _evict)
    await cleanup_module.cancellation_safe_evict_then_dispose(
        binding=object(), dispose=_dispose, reason="swarmflow-worker-finish", owner_id="wf-worker-0"
    )
    assert events == ["evict:swarmflow-worker-finish:wf-worker-0", "dispose"]


@pytest.mark.asyncio
async def test_worker_cleanup_reraises_cancelled_error_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose_started = asyncio.Event()
    allow_dispose = asyncio.Event()

    async def _evict(binding: object, *, reason: str, worker_id: str) -> bool:
        return True

    async def _dispose() -> None:
        dispose_started.set()
        await allow_dispose.wait()

    monkeypatch.setattr(cleanup_module, "cancellation_safe_best_effort_evict", _evict)
    task = asyncio.create_task(
        cleanup_module.cancellation_safe_evict_then_dispose(
            binding=object(), dispose=_dispose, reason="swarmflow-worker-finish",
            owner_id="wf-worker-0", timeout=1.0,
        )
    )
    await dispose_started.wait()
    task.cancel()
    allow_dispose.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_worker_cleanup_timeout_cancels_internal_task_without_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hanging_evict(binding: object, *, reason: str, worker_id: str) -> bool:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _dispose() -> None:
        raise AssertionError("dispose must wait for evict")

    monkeypatch.setattr(cleanup_module, "cancellation_safe_best_effort_evict", _hanging_evict)
    await cleanup_module.cancellation_safe_evict_then_dispose(
        binding=object(), dispose=_dispose, reason="swarmflow-worker-finish",
        owner_id="wf-worker-0", timeout=0.01,
    )
    assert started.is_set()
    assert cancelled.is_set()
    current = asyncio.current_task()
    assert [task for task in asyncio.all_tasks() if task is not current and not task.done()] == []
