# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Full TeamWorkerBackend KVC cleanup tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.kv_cache import kv_cache_harness_session_lifecycle_hook
from openjiuwen.agent_teams.kv_cache import kv_cache_cleanup as cleanup_module
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.core.kv_cache import KVCacheAffinityConfig, KVCacheIdentity
from openjiuwen.core.kv_cache.kv_cache_runtime import KVCacheRuntime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


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
    def __init__(
        self,
        events: list[str],
        *,
        member_name: str,
        outcome: str,
        block_cancel: asyncio.Event | None = None,
    ) -> None:
        self.events = events
        self.member_name = member_name
        self.outcome = outcome
        self.block_cancel = block_cancel
        self.model = _WorkerModel(events)
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=True)
        )
        self.identities: list[KVCacheIdentity] = []

    def add_rail(self, rail: Any) -> None:
        return None

    async def run_once(self, content: Any, **kwargs: Any) -> dict[str, Any]:
        team_session = kwargs["team_session"]
        session = team_session.create_agent_session(
            card=AgentCard(id=self.member_name, name=self.member_name),
            share_stream_writer=False,
        )
        kv_cache_harness_session_lifecycle_hook.on_harness_session_created(self, session)
        self.identities.append(session.get_cache_identity())
        try:
            manageable = (
                self.deep_config.kv_cache_affinity_config.enable_kv_cache_affinity
                and self.model.supports_kv_cache_affinity()
            )
        except Exception:
            manageable = False
        runtime = session.get_kv_cache_runtime()
        if manageable and runtime is not None:
            await runtime.register_binding(session.get_cache_identity(), self.model)
        self.events.append("inference")
        try:
            if self.outcome == "failure":
                raise ValueError("business failed")
            if self.outcome == "cancel":
                assert self.block_cancel is not None
                await self.block_cancel.wait()
            return {"output": "ok"}
        finally:
            await kv_cache_harness_session_lifecycle_hook.after_harness_session_finished(self, session)

    async def dispose(self) -> None:
        self.events.append("dispose")


def _backend(monkeypatch: pytest.MonkeyPatch, harnesses: list[_FullWorkerHarness], *, outcome: str, block_cancel: asyncio.Event | None = None) -> TeamWorkerBackend:
    from openjiuwen.agent_teams.harness import team_harness as th_mod
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

    def _fake_build(**kwargs: Any) -> _FullWorkerHarness:
        harness = _FullWorkerHarness(
            [],
            member_name=kwargs["member_name"],
            outcome=outcome,
            block_cancel=block_cancel,
        )
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
    backend._kv_cache_runtime = KVCacheRuntime(
        binding_provider=lambda: harnesses[0].model if harnesses else None
    )
    return backend


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

    def _fake_build(**kwargs: Any) -> _FullWorkerHarness:
        harness = _FullWorkerHarness(
            [], member_name=kwargs["member_name"], outcome="success"
        )
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

    def _manageable_model() -> Any:
        if not harnesses:
            return None
        try:
            return (
                harnesses[0].model
                if harnesses[0].model.supports_kv_cache_affinity()
                else None
            )
        except Exception:
            return None

    backend._kv_cache_runtime = KVCacheRuntime(binding_provider=_manageable_model)

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

    def _fake_build(**kwargs: Any) -> _FullWorkerHarness:
        harness = _FullWorkerHarness(
            [], member_name=kwargs["member_name"], outcome="success"
        )

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
    def _manageable_model() -> Any:
        if not harnesses:
            return None
        try:
            return (
                harnesses[0].model
                if harnesses[0].model.supports_kv_cache_affinity()
                else None
            )
        except Exception:
            return None

    backend._kv_cache_runtime = KVCacheRuntime(binding_provider=_manageable_model)

    result = await backend._execute_worker("prompt", [], member_name="wf-worker-0", has_schema=False, model=None)

    assert result == "ok"
    assert len(harnesses[0].identities) == 1
    assert harnesses[0].model.evict_identities == []
    assert harnesses[0].events == ["inference", "dispose"]


@pytest.mark.asyncio
async def test_worker_cleanup_runs_evict_before_dispose(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def _release() -> bool:
        events.append("release")
        return True

    async def _dispose() -> None:
        events.append("dispose")

    await cleanup_module.cancellation_safe_release_then_dispose(
        release_kvc=_release,
        dispose=_dispose,
        owner_id="wf-worker-0",
    )
    assert events == ["release", "dispose"]


@pytest.mark.asyncio
async def test_worker_cleanup_reraises_cancelled_error_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose_started = asyncio.Event()
    allow_dispose = asyncio.Event()

    async def _release() -> bool:
        return True

    async def _dispose() -> None:
        dispose_started.set()
        await allow_dispose.wait()

    task = asyncio.create_task(
        cleanup_module.cancellation_safe_release_then_dispose(
            release_kvc=_release,
            dispose=_dispose,
            owner_id="wf-worker-0",
            timeout=1.0,
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

    async def _hanging_release() -> bool:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _dispose() -> None:
        raise AssertionError("dispose must wait for evict")

    await cleanup_module.cancellation_safe_release_then_dispose(
        release_kvc=_hanging_release,
        dispose=_dispose,
        owner_id="wf-worker-0",
        timeout=0.01,
    )
    assert started.is_set()
    assert cancelled.is_set()
    current = asyncio.current_task()
    assert [task for task in asyncio.all_tasks() if task is not current and not task.done()] == []
