# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workflow-terminal KVC cleanup coverage for stateful swarmflow workers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.engine import run_workflow
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig, KVCacheIdentity
from openjiuwen.core.session.agent import Session


class _TerminalModel:
    def __init__(self, events: list[str], *, fail_evict: bool = False) -> None:
        self.events = events
        self.fail_evict = fail_evict
        self.evict_calls: list[dict[str, Any]] = []
        self.offload_calls: list[dict[str, Any]] = []
        self.prefetch_calls: list[dict[str, Any]] = []

    def supports_kv_cache_affinity(self) -> bool:
        return True

    async def evict_kvc(self, **kwargs: Any) -> bool:
        self.events.append("evict")
        self.evict_calls.append(dict(kwargs))
        if self.fail_evict:
            raise RuntimeError("cleanup evict failed")
        return True

    async def offload_kvc(self, **kwargs: Any) -> bool:
        self.events.append("offload")
        self.offload_calls.append(dict(kwargs))
        return True

    async def prefetch_kvc(self, **kwargs: Any) -> bool:
        self.events.append("prefetch")
        self.prefetch_calls.append(dict(kwargs))
        return True


class _TerminalHarness:
    def __init__(
        self,
        events: list[str],
        *,
        fail_evict: bool = False,
        block_send: asyncio.Event | None = None,
        send_started: asyncio.Event | None = None,
    ) -> None:
        self.events = events
        self.model = _TerminalModel(events, fail_evict=fail_evict)
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=True)
        )
        self.block_send = block_send
        self.send_started = send_started
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
        self.events.append("send")
        if self.send_started is not None:
            self.send_started.set()
        if self.block_send is not None:
            await self.block_send.wait()
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
        self.events.append("dispose")


def _write(tmp_path: Path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    harnesses: list[_TerminalHarness],
    events: list[str],
    *,
    fail_evict: bool = False,
    block_send: asyncio.Event | None = None,
    send_started: asyncio.Event | None = None,
) -> TeamWorkerBackend:
    from openjiuwen.agent_teams.harness import team_harness as team_harness_module

    def _build(**_: Any) -> _TerminalHarness:
        harness = _TerminalHarness(
            events,
            fail_evict=fail_evict,
            block_send=block_send,
            send_started=send_started,
        )
        harnesses.append(harness)
        return harness

    monkeypatch.setattr(team_harness_module.TeamHarness, "build", _build)
    base = DeepAgentSpec(
        tools=[],
        kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=True),
    )
    return TeamWorkerBackend(
        model=None,
        worker_base_spec=base,
        team_name="team-a",
        session_id="team-session-a",
        run_id="run-a",
    )


@pytest.mark.asyncio
async def test_stateful_workflow_success_aclose_evicts_then_disposes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _write(
        tmp_path,
        "stateful_success.py",
        """
from swarmflow import agent_session
META = {"name": "stateful-success", "description": "", "phases": []}
async def run(args):
    s = agent_session(label="advisor")
    answer = await s.send("hello")
    return {"answer": answer}
""",
    )
    events: list[str] = []
    harnesses: list[_TerminalHarness] = []
    backend = _backend(monkeypatch, harnesses, events)

    result = await run_workflow(script, backend=backend)

    assert result == {"answer": "reply:hello"}
    assert events == ["start", "send", "evict", "dispose"]
    assert harnesses[0].model.offload_calls == []
    assert harnesses[0].model.prefetch_calls == []
    assert len(harnesses[0].model.evict_calls) == 1


@pytest.mark.asyncio
async def test_stateful_workflow_failure_aclose_preserves_business_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _write(
        tmp_path,
        "stateful_failure.py",
        """
from swarmflow import agent_session
META = {"name": "stateful-failure", "description": "", "phases": []}
async def run(args):
    s = agent_session(label="advisor")
    await s.send("hello")
    raise RuntimeError("business boom")
""",
    )
    events: list[str] = []
    harnesses: list[_TerminalHarness] = []
    backend = _backend(monkeypatch, harnesses, events, fail_evict=True)

    with pytest.raises(RuntimeError, match="business boom"):
        await run_workflow(script, backend=backend)

    assert events == ["start", "send", "evict", "dispose"]
    assert len(harnesses[0].model.evict_calls) == 1


@pytest.mark.asyncio
async def test_stateful_workflow_cancel_aclose_preserves_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _write(
        tmp_path,
        "stateful_cancel.py",
        """
from swarmflow import agent_session
META = {"name": "stateful-cancel", "description": "", "phases": []}
async def run(args):
    s = agent_session(label="advisor")
    return await s.send("block")
""",
    )
    events: list[str] = []
    harnesses: list[_TerminalHarness] = []
    send_started = asyncio.Event()
    block_send = asyncio.Event()
    backend = _backend(
        monkeypatch,
        harnesses,
        events,
        fail_evict=True,
        block_send=block_send,
        send_started=send_started,
    )

    task = asyncio.create_task(run_workflow(script, backend=backend))
    await send_started.wait()
    task.cancel()
    block_send.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert events == ["start", "send", "evict", "dispose"]
    assert len(harnesses[0].model.evict_calls) == 1
