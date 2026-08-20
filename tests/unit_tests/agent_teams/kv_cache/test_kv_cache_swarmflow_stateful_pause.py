# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pause/unwind/resume KVC coverage for stateful swarmflow workers."""

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


class _PauseModel:
    def __init__(self, events: list[str], *, fail_evict: bool) -> None:
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
            raise RuntimeError("evict failed")
        return True

    async def offload_kvc(self, **kwargs: Any) -> bool:
        self.events.append("offload")
        self.offload_calls.append(dict(kwargs))
        return True

    async def prefetch_kvc(self, **kwargs: Any) -> bool:
        self.events.append("prefetch")
        self.prefetch_calls.append(dict(kwargs))
        return True


class _PauseHarness:
    def __init__(
        self,
        events: list[str],
        *,
        fail_evict: bool,
        block_send: asyncio.Event | None,
        send_started: asyncio.Event | None,
    ) -> None:
        self.events = events
        self.model = _PauseModel(events, fail_evict=fail_evict)
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

    async def abort(self, *, immediate: bool = False) -> None:
        self.events.append(f"abort:{immediate}")

    async def dispose(self) -> None:
        self.events.append("dispose")


def _write(tmp_path: Path) -> str:
    path = tmp_path / "stateful_pause.py"
    path.write_text(
        """
from swarmflow import agent_session
META = {"name": "stateful-pause", "description": "", "phases": []}
async def run(args):
    s = agent_session(label="advisor")
    return await s.send("work")
""",
        encoding="utf-8",
    )
    return str(path)


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    harnesses: list[_PauseHarness],
    events: list[str],
    *,
    fail_evict: bool = False,
    block_send: asyncio.Event | None = None,
    send_started: asyncio.Event | None = None,
) -> TeamWorkerBackend:
    from openjiuwen.agent_teams.harness import team_harness as team_harness_module

    def _build(**_: Any) -> _PauseHarness:
        harness = _PauseHarness(
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
@pytest.mark.parametrize("fail_evict", [False, True])
async def test_stateful_pause_unwinds_cleanup_and_resume_cold_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fail_evict: bool,
) -> None:
    script = _write(tmp_path)
    journal = str(tmp_path / "journal.jsonl")
    pause_events: list[str] = []
    pause_harnesses: list[_PauseHarness] = []
    send_started = asyncio.Event()
    block_send = asyncio.Event()
    pause_backend = _backend(
        monkeypatch,
        pause_harnesses,
        pause_events,
        fail_evict=fail_evict,
        block_send=block_send,
        send_started=send_started,
    )
    abort_event = asyncio.Event()

    task = asyncio.create_task(
        run_workflow(script, backend=pause_backend, journal_path=journal, abort_event=abort_event)
    )
    await send_started.wait()
    abort_event.set()
    await pause_backend.abort_sessions()
    task.cancel()
    block_send.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Pause cancels the workflow task; engine-runner finally reaches aclose,
    # making the old avatar physically terminal. KVC follows that existing
    # dispose point with evict, without introducing pause offload/prefetch.
    assert pause_events == ["start", "send", "abort:True", "evict", "dispose"]
    old_harness = pause_harnesses[0]
    old_identity = old_harness.started_identity
    assert old_identity is not None
    assert old_identity.parent_cache_id == "team-session-a"
    assert old_harness.model.offload_calls == []
    assert old_harness.model.prefetch_calls == []
    assert len(old_harness.model.evict_calls) == 1
    await pause_backend.aclose()
    assert pause_events == ["start", "send", "abort:True", "evict", "dispose"]

    resume_events: list[str] = []
    resume_harnesses: list[_PauseHarness] = []
    resume_backend = _backend(monkeypatch, resume_harnesses, resume_events)

    result = await run_workflow(script, backend=resume_backend, journal_path=journal, resume=journal)

    assert result == "reply:work"
    assert resume_events == ["start", "send", "evict", "dispose"]
    assert len(resume_harnesses) == 1
    new_harness = resume_harnesses[0]
    assert new_harness is not old_harness
    assert new_harness.started_identity is not None
    assert new_harness.started_identity.cache_id != old_identity.cache_id
    assert new_harness.started_identity.parent_cache_id == "team-session-a"
    assert new_harness.model.offload_calls == []
    assert new_harness.model.prefetch_calls == []
    assert len(new_harness.model.evict_calls) == 1
