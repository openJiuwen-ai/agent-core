# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Engine-layer tests for the ``AgentSession`` model first-lock.

One session owns one avatar, and an avatar is bound to one model: the first
live turn locks the session's resolved model identity; a later live turn whose
hints resolve differently raises :class:`EngineError` pointing at ``fork()``.
Pure cache-hit replays never open the avatar, so they never lock — a resume
whose later turns pick a different hint locks that hint afresh. Fork children
build a fresh session, so they lock independently of the parent.
"""

from __future__ import annotations

import asyncio

import pytest

from openjiuwen.agent_teams.workflow.engine import EngineError, run_workflow
from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.errors import BackendError


class _SessionBackend(AgentBackend):
    """Session-capable fake: counts opens/turns, echoes the prompt.

    Enough of the stateful-session surface for the engine's open/send/fork
    plumbing; no model resolution (the lock identity is computed engine-side).
    """

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.turns: list[str] = []
        self.forks: list[str] = []
        self.fork_data: dict | None = {"messages": [{"role": "user", "content": "forked"}]}
        self._n = 0

    async def run(self, prompt, opts, schema_json, *, call_key=None):
        return AgentResult(text=f"ran:{prompt}")

    async def ensure_member_name(self, *, kind, opts):
        name = f"m{self._n}"
        self._n += 1
        return name

    async def open_session(self, *, kind, instructions, opts, fork_data=None, member_name=None):
        sid = member_name or f"s{self._n}"
        self.opened.append(sid)
        return sid

    async def send_turn(self, session_id, prompt, opts, schema_json, *, history=(), correlation_id=None):
        self.turns.append(prompt)
        return AgentResult(text=f"turn:{prompt}")

    async def close_session(self, session_id):
        return None

    async def capture_fork(self, session_id, *, keep_rounds=None, fork_mode="full"):
        self.forks.append(session_id)
        return self.fork_data


class _FlakySessionBackend(_SessionBackend):
    """Session fake that rejects one named turn, then recovers.

    The rejection is a non-retryable :class:`BackendError` (like a model-option
    rule violation on launch), so the engine surfaces ``AGENT_FAILED`` instead
    of burning retry attempts on it.
    """

    def __init__(self, fail_prompt: str) -> None:
        super().__init__()
        self._fail_prompt = fail_prompt

    async def send_turn(self, session_id, prompt, opts, schema_json, *, history=(), correlation_id=None):
        if prompt == self._fail_prompt:
            raise BackendError("model hint rejected: unsupported model", retryable=False)
        return await super().send_turn(
            session_id, prompt, opts, schema_json, history=history, correlation_id=correlation_id
        )


def _write(tmp_path, name: str, src: str) -> str:
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return str(path)


_SWITCH_MODEL_SCRIPT = """
from swarmflow import agent_session

META = {"name": "switch-model", "description": "session model switch", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "builtin_model": "gpt-5-codex"})
    await s.send("first")
    await s.send("second")
    await s.send("third", options={"builtin_model": None, "model": "deepseek-v4-flash"})
    return None
"""


def test_third_party_turn_switching_model_raises(tmp_path):
    """A session locked to ``builtin_model`` raises on a later turn hinting ``model``."""
    script = _write(tmp_path, "switch_model.py", _SWITCH_MODEL_SCRIPT)
    backend = _SessionBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "fork()" in str(exc.value)
    assert "gpt-5-codex" in str(exc.value)  # names the locked value
    # The first two turns ran; the switching turn never reached the backend.
    assert backend.turns == ["first", "second"]


_SAME_HINT_SCRIPT = """
from swarmflow import agent_session

META = {"name": "same-hint", "description": "stable model", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "model": "glm-5"})
    a = await s.send("first")
    b = await s.send("second", options={"model": "glm-5"})
    c = await s.send("third")
    return [a, b, c]
"""


def test_same_hint_every_turn_executes(tmp_path):
    """Same hint every turn is a no-op lock — all turns run."""
    script = _write(tmp_path, "same_hint.py", _SAME_HINT_SCRIPT)
    backend = _SessionBackend()

    result = asyncio.run(run_workflow(script, backend=backend))
    assert result == ["turn:first", "turn:second", "turn:third"]
    assert backend.turns == ["first", "second", "third"]


_SUPPLY_SWITCH_SCRIPT = """
from swarmflow import agent_session

META = {"name": "supply-switch", "description": "supply switch", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "model": "X"})
    await s.send("first")
    await s.send("second", options={"model": None, "builtin_model": "X"})
    return None
"""


def test_same_string_different_supply_raises(tmp_path):
    """``model='X'`` and ``builtin_model='X'`` are different models, so they differ."""
    script = _write(tmp_path, "supply_switch.py", _SUPPLY_SWITCH_SCRIPT)
    backend = _SessionBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "fork()" in str(exc.value)
    assert backend.turns == ["first"]


_FORK_LOCK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "fork-lock", "description": "fork independent lock", "phases": []}

async def run(args):
    parent = agent_session(label="p", options={"agent_type": "codex", "model": "glm-5"})
    await parent.send("p1")
    child = await parent.fork(label="c", options={"model": "deepseek-v4-flash"})
    await child.send("c1")
    await child.send("c2")        # child locked to deepseek; same hint is fine
    await parent.send("p2")       # parent locked to glm-5; same hint is fine
    await child.send("c3", options={"model": "glm-5"})   # violates the CHILD's lock
    return None
"""


def test_fork_child_locks_independently(tmp_path):
    """A fork child with a different hint runs fine; its later turns validate against its own lock."""
    script = _write(tmp_path, "fork_lock.py", _FORK_LOCK_SCRIPT)
    backend = _SessionBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    # The error names the CHILD's locked value, not the parent's.
    assert "deepseek-v4-flash" in str(exc.value)
    assert "fork()" in str(exc.value)
    # p1, c1, c2, p2 all ran (p2 same-hint on the parent's glm-5 lock); c3 blocked.
    assert backend.turns == ["p1", "c1", "c2", "p2"]


_FORK_PARENT_LOCK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "fork-parent-lock", "description": "parent keeps lock", "phases": []}

async def run(args):
    parent = agent_session(label="p", options={"agent_type": "codex", "model": "glm-5"})
    await parent.send("p1")
    child = await parent.fork(label="c", options={"model": "deepseek-v4-flash"})
    await child.send("c1")
    await parent.send("p2", options={"model": "deepseek-v4-flash"})   # parent locked to glm-5
    return None
"""


def test_parent_keeps_its_lock_after_fork(tmp_path):
    """The child's different hint never leaks into the parent's own lock."""
    script = _write(tmp_path, "fork_parent_lock.py", _FORK_PARENT_LOCK_SCRIPT)
    backend = _SessionBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    # The error names the PARENT's locked value — the child's hint did not touch it.
    assert "glm-5" in str(exc.value)
    assert backend.turns == ["p1", "c1"]


def test_cache_hit_replay_does_not_lock_then_live_turn_locks(tmp_path):
    """Replayed (cache-hit) turns never lock; a later live turn locks its own hint."""
    journal = str(tmp_path / "run.jsonl")
    two_turn = """
from swarmflow import agent_session

META = {"name": "replay-lock", "description": "replay then live", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "model": "glm-5"})
    await s.send("first")
    await s.send("second")
    return None
"""
    asyncio.run(
        run_workflow(_write(tmp_path, "v1.py", two_turn), backend=_SessionBackend(), journal_path=journal)
    )

    three_turn = two_turn.replace(
        'await s.send("second")',
        'await s.send("second", options={"model": "deepseek-v4-flash"})',
    )
    backend = _SessionBackend()
    result = asyncio.run(run_workflow(_write(tmp_path, "v2.py", three_turn), backend=backend, resume=journal))

    # Turn 1 replayed (no lock, no avatar open); turn 2 is live and locks
    # deepseek-v4-flash — no spurious error from the replayed glm-5 turn.
    assert backend.opened == ["m0"]
    assert backend.turns == ["second"]
    assert result is None


_NATIVE_LOCK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "native-lock", "description": "native model lock", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"model": "glm-5"})
    await s.send("first")
    await s.send("second", options={"model": "deepseek-v4-flash"})
    return None
"""


def test_native_session_locks_its_model_hint(tmp_path):
    """Native sessions lock the native ``model`` hint too (one avatar = one model)."""
    script = _write(tmp_path, "native_lock.py", _NATIVE_LOCK_SCRIPT)
    backend = _SessionBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "glm-5" in str(exc.value)
    assert "fork()" in str(exc.value)
    assert backend.turns == ["first"]


_BASE_LOCK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "base-lock", "description": "no-hint lock", "phases": []}

async def run(args):
    s = agent_session(label="chat")
    await s.send("first")
    await s.send("second", options={"model": "glm-5"})
    return None
"""


class _BaseModelNamingBackend(_SessionBackend):
    """Adds the describe_base_model capability hook (names the base spec model)."""

    def describe_base_model(self) -> str:
        return "glm-5-air"


_THIRD_PARTY_NO_HINT_SCRIPT = """
from swarmflow import agent_session

META = {"name": "cli-default-lock", "description": "third-party no hint", "phases": []}

async def run(args):
    s = agent_session(label="cx", options={"agent_type": "codex"})
    a = await s.send("t1")
    b = await s.send("t2", options={"model": "glm-5"})
    return [a, b]
"""


def test_no_hint_native_lock_names_concrete_base_model(tmp_path):
    """A backend exposing describe_base_model: the lock error names that model."""
    script = _write(tmp_path, "concrete_lock.py", _BASE_LOCK_SCRIPT)
    backend = _BaseModelNamingBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "glm-5-air" in str(exc.value)
    assert "fork()" in str(exc.value)
    assert backend.turns == ["first"]


def test_no_hint_third_party_lock_names_the_cli_default(tmp_path):
    """A third-party no-hint lock names the CLI's own default model honestly."""
    script = _write(tmp_path, "cli_default_lock.py", _THIRD_PARTY_NO_HINT_SCRIPT)
    backend = _SessionBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "codex CLI's own default model" in str(exc.value)
    assert "fork()" in str(exc.value)
    assert backend.turns == ["t1"]


def test_no_hint_lock_names_base_spec_model(tmp_path):
    """A no-hint first turn locks the avatar's default; the message says 'the base spec model'."""
    script = _write(tmp_path, "base_lock.py", _BASE_LOCK_SCRIPT)
    backend = _SessionBackend()

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "the base spec model" in str(exc.value)
    assert "fork()" in str(exc.value)
    assert backend.turns == ["first"]


_HUMAN_LOCK_SCRIPT = """
from swarmflow import human_session

META = {"name": "human-lock", "description": "human no lock", "phases": []}

async def run(args):
    h = human_session(label="lead", options={"model": "glm-5"})
    a = await h.send("h1")
    b = await h.send("h2", options={"model": "deepseek-v4-flash"})
    return [a, b]
"""


def test_human_sessions_do_not_lock(tmp_path):
    """Human sessions answer through a person, not a model — hint changes never lock."""
    script = _write(tmp_path, "human_lock.py", _HUMAN_LOCK_SCRIPT)
    backend = _SessionBackend()

    result = asyncio.run(run_workflow(script, backend=backend))
    assert result == ["turn:h1", "turn:h2"]


_ONESHOT_SCRIPT = """
from swarmflow import agent

META = {"name": "one-shot", "description": "stateless agents", "phases": []}

async def run(args):
    a = await agent("a", options={"model": "glm-5"})
    b = await agent("b", options={"model": "deepseek-v4-flash"})
    return [a, b]
"""


def test_native_one_shot_agent_not_locked(tmp_path):
    """Stateless one-shot ``agent()`` calls are exempt from the session lock."""
    script = _write(tmp_path, "one_shot.py", _ONESHOT_SCRIPT)
    backend = _SessionBackend()

    result = asyncio.run(run_workflow(script, backend=backend))
    assert result == ["ran:a", "ran:b"]


_SWITCH_AFTER_FAIL_SCRIPT = """
from swarmflow import agent_session

META = {"name": "switch-after-fail", "description": "relock after failed first turn", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "model": "glm-5"})
    await s.send("first")                                        # backend rejects -> AGENT_FAILED
    await s.send("second", options={"model": "deepseek-v4-flash"})  # the lock never landed: re-lock
    await s.send("third", options={"model": "claude-4"})         # now locked to deepseek: raises
    return None
"""


def test_failed_first_turn_then_different_hint_relocks(tmp_path):
    """A first turn that failed never "used" the lock, so the corrected hint re-locks."""
    script = _write(tmp_path, "switch_after_fail.py", _SWITCH_AFTER_FAIL_SCRIPT)
    backend = _FlakySessionBackend(fail_prompt="first")

    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    # The error names the RE-LOCKED value, not the one from the failed turn.
    assert "deepseek-v4-flash" in str(exc.value)
    assert "fork()" in str(exc.value)
    assert backend.opened == ["m0"]          # one avatar across all three turns
    assert backend.turns == ["second"]       # first failed, second ran, third blocked


_RETRY_SAME_HINT_SCRIPT = """
from swarmflow import agent_session

META = {"name": "retry-same-hint", "description": "same hint after failed first turn", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "model": "glm-5"})
    a = await s.send("first")    # backend rejects -> AGENT_FAILED
    b = await s.send("second")   # same hint (inherits the session lock): no-op, proceeds
    return [a, b]
"""


def test_failed_first_turn_then_same_hint_proceeds(tmp_path):
    """The same hint after a failed first turn stays on the no-op lock path."""
    script = _write(tmp_path, "retry_same_hint.py", _RETRY_SAME_HINT_SCRIPT)
    backend = _FlakySessionBackend(fail_prompt="first")

    result = asyncio.run(run_workflow(script, backend=backend))
    assert result == [None, "turn:second"]
    assert backend.turns == ["second"]
