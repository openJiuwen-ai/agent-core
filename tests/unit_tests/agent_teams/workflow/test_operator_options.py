# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for operator-options governance (SDD-0020).

model route: the engine fails fast on an ``agent(model=...)`` hint the backend
cannot honor — before the signature, the start event and the journal — and a
session's model is fixed at its first cache-miss turn (a later different hint
raises with fork() guidance). timeout route: a timed-out attempt's failure
message carries the budget ("timed out after Ns") through the existing
error_detail pipeline, with the retry control flow unchanged.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.workflow.engine import (
    EngineError,
    ProgressKind,
    run_workflow,
)
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult


class _PoolBackend(AgentBackend):
    """Backend with a declared model pool; records every model hint it sees.

    ``pool=None`` reproduces a backend without a pool concept (the historical
    default): the engine must skip validation entirely.
    """

    def __init__(self, pool: list[str] | None) -> None:
        super().__init__()
        self.pool = pool
        self.attempts = 0  # run() invocations that actually started
        self.ran: list[str | None] = []  # single-shot model hints (completed calls)
        self.turns: list[str | None] = []  # send_turn model hints
        self.opened: list[str | None] = []  # open_session model hints
        self.sleep_s = 0.0  # >0 → run() stalls past the caller's timeout

    def model_pool_names(self) -> list[str] | None:
        return self.pool

    async def run(
        self, prompt: str, opts: dict, schema_json: dict | None, *, call_key: str | None = None
    ) -> AgentResult:
        self.attempts += 1
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        self.ran.append(opts.get("model"))
        return AgentResult(text=f"ran:{prompt}")

    async def ensure_member_name(self, *, kind: str, opts: dict) -> str:
        return "m0"

    async def open_session(
        self,
        *,
        kind: str,
        instructions: str | None,
        opts: dict,
        fork_data: dict | None = None,
        member_name: str | None = None,
    ) -> str:
        self.opened.append(opts.get("model"))
        return "s0"

    async def send_turn(
        self,
        session_id: str,
        prompt: str,
        opts: dict,
        schema_json: dict | None,
        *,
        history=(),
        correlation_id: str | None = None,
    ) -> AgentResult:
        self.turns.append(opts.get("model"))
        return AgentResult(text=f"turn:{prompt}")

    async def close_session(self, session_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def capture_fork(self, session_id: str, *, keep_rounds: int | None, fork_mode: str) -> dict | None:
        return None


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


_BAD_MODEL_SCRIPT = """
from swarmflow import agent

META = {"name": "bad-model", "description": "unknown model hint", "phases": []}

async def run(args):
    return await agent("hi", options={"model": "nope"})
"""

_GOOD_MODEL_SCRIPT = """
from swarmflow import agent

META = {"name": "good-model", "description": "pool model hint", "phases": []}

async def run(args):
    return await agent("hi", options={"model": "deepseek-v4"})
"""

_SESSION_SWITCH_MODEL_SCRIPT = """
from swarmflow import agent_session

META = {"name": "switch", "description": "mid-session model switch", "phases": []}

async def run(args):
    s = agent_session(label="chat")
    a = await s.send("first", options={"model": "m1"})
    b = await s.send("second", options={"model": "m2"})
    return [a, b]
"""

_SESSION_SAME_MODEL_SCRIPT = """
from swarmflow import agent_session

META = {"name": "same", "description": "same model both turns", "phases": []}

async def run(args):
    s = agent_session(label="chat")
    a = await s.send("first", options={"model": "m1"})
    b = await s.send("second", options={"model": "m1"})
    return [a, b]
"""

_FORK_SWITCH_MODEL_SCRIPT = """
from swarmflow import agent_session

META = {"name": "fork-switch", "description": "fork to switch model", "phases": []}

async def run(args):
    s = agent_session(label="parent")
    a = await s.send("one", options={"model": "m1"})
    child = await s.fork(options={"model": "m2"})
    b = await child.send("two", options={"model": "m2"})
    return [a, b]
"""

_TIMEOUT_SCRIPT = """
from swarmflow import agent

META = {"name": "slow", "description": "timeout message", "phases": []}

async def run(args):
    v = await agent("slow", options={"timeout": 0.05})
    return ["continued", v]
"""


# ---------------------------------------------------------------- model route


def test_unknown_model_hint_fails_fast(tmp_path):
    """An out-of-pool hint raises EngineError before any backend call."""
    script = _write(tmp_path, "bad.py", _BAD_MODEL_SCRIPT)

    with pytest.raises(EngineError, match="not in the model pool"):
        asyncio.run(run_workflow(script, backend=_PoolBackend(["deepseek-v4"])))


def test_error_message_lists_available_models(tmp_path):
    """The failure message names the pool contents so the author can fix it."""
    script = _write(tmp_path, "bad2.py", _BAD_MODEL_SCRIPT)

    with pytest.raises(EngineError, match=r"available.*deepseek-v4"):
        asyncio.run(run_workflow(script, backend=_PoolBackend(["deepseek-v4", "glm-5"])))


def test_valid_model_hint_runs_normally(tmp_path):
    """An in-pool hint passes validation and reaches the backend unchanged."""
    script = _write(tmp_path, "good.py", _GOOD_MODEL_SCRIPT)
    backend = _PoolBackend(["deepseek-v4"])

    result = asyncio.run(run_workflow(script, backend=backend))

    assert result == "ran:hi"
    assert backend.ran == ["deepseek-v4"]


def test_backend_without_pool_skips_validation(tmp_path):
    """pool=None (MockBackend / old-style backends) → any hint passes, byte-for-byte old behavior."""
    script = _write(tmp_path, "nopool.py", _BAD_MODEL_SCRIPT)
    backend = _PoolBackend(None)

    result = asyncio.run(run_workflow(script, backend=backend))

    assert result == "ran:hi"
    assert backend.ran == ["nope"]


def test_session_second_turn_different_model_raises(tmp_path):
    """A session's model is fixed at the first turn; a different later hint raises."""
    script = _write(tmp_path, "switch.py", _SESSION_SWITCH_MODEL_SCRIPT)

    with pytest.raises(EngineError, match="fixed at the first turn"):
        asyncio.run(run_workflow(script, backend=_PoolBackend(["m1", "m2"])))


def test_session_same_model_second_turn_ok(tmp_path):
    """Re-sending the same model hint is a no-op, not an error."""
    script = _write(tmp_path, "same.py", _SESSION_SAME_MODEL_SCRIPT)
    backend = _PoolBackend(["m1"])

    result = asyncio.run(run_workflow(script, backend=backend))

    assert result == ["turn:first", "turn:second"]
    assert backend.turns == ["m1", "m1"]


def test_fork_child_session_can_switch_model(tmp_path):
    """fork() is the sanctioned way to continue on a different model."""
    script = _write(tmp_path, "fork.py", _FORK_SWITCH_MODEL_SCRIPT)
    backend = _PoolBackend(["m1", "m2"])

    result = asyncio.run(run_workflow(script, backend=backend))

    assert result == ["turn:one", "turn:two"]
    assert backend.opened == ["m1", "m2"]


def test_resolver_defense_warning_dedup(monkeypatch):
    """A hint that passes validation but misses at resolution warns once per name."""
    from openjiuwen.agent_teams.agent import agent_configurator
    from openjiuwen.agent_teams.models import allocator

    def _always_none(spec, *, model_name, model_index):
        return None  # simulate the pool shrinking between validation and resolution

    monkeypatch.setattr(allocator, "resolve_member_model", _always_none)

    recorded: list[str] = []

    class _Logger:
        def warning(self, msg, *args):
            recorded.append(msg % args if args else msg)

    monkeypatch.setattr(agent_configurator, "team_logger", _Logger())

    resolver = agent_configurator._SwarmflowModelResolver(
        SimpleNamespace(model_pool=[SimpleNamespace(model_name="m1")])
    )

    assert resolver("m1") is None
    assert resolver("m1") is None  # same name again → deduped, no second warning
    assert len(recorded) == 1
    assert "m1" in recorded[0]
    assert "pool" in recorded[0]
    assert resolver.pool_names() == ["m1"]


# -------------------------------------------------------------- timeout route


def _run_timeout_case(tmp_path, backend):
    script = _write(tmp_path, "slow.py", _TIMEOUT_SCRIPT)
    events = []
    result = asyncio.run(run_workflow(script, backend=backend, progress_sink=events.append))
    failed = [e for e in events if e.kind == ProgressKind.AGENT_FAILED]
    return result, failed


def test_timeout_failure_message_carries_budget(tmp_path):
    """AGENT_FAILED's message names the timeout budget via the existing pipeline."""
    backend = _PoolBackend(None)
    backend.sleep_s = 0.5

    result, failed = _run_timeout_case(tmp_path, backend)

    assert result == ["continued", None]  # agent-level failure, script continues
    assert len(failed) == 1
    assert "timed out after 0.05s" in failed[0].message


def test_timeout_retry_control_flow_unchanged(tmp_path):
    """Still retried to the full attempt count; the failure is agent-level."""
    backend = _PoolBackend(None)
    backend.sleep_s = 0.5

    result, failed = _run_timeout_case(tmp_path, backend)

    assert backend.attempts == 3  # rt.retries=2 default → first try + 2 retries
    assert "failed after 3 attempts" in failed[0].message
    assert "timed out after 0.05s" in failed[0].message
