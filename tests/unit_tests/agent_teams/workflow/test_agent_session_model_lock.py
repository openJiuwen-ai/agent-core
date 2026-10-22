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
        self.opened_opts: list[dict] = []
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
        self.opened_opts.append(dict(opts))
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


_LOCK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "lock", "description": "session model lock", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "builtin_model": "gpt-5-codex"})
    a = await s.send("first")
    b = await s.send("second", options={"builtin_model": "gpt-5-codex"})
    c = await s.send("third", options=THIRD)
    return [a, b, c]
"""


@pytest.mark.parametrize(
    ("third", "locked_name"),
    [
        ('{"builtin_model": None, "model": "deepseek-v4-flash"}', "gpt-5-codex"),  # different model
        ('{"builtin_model": None, "model": "gpt-5-codex"}', "gpt-5-codex"),        # same string, other supply
    ],
)
def test_turn_with_a_different_identity_raises_and_names_the_lock(tmp_path, third, locked_name):
    """The first live turn locks the session's model identity; a later turn whose
    hints resolve to another identity raises, naming the locked value and fork().
    ``model='X'`` and ``builtin_model='X'`` are different identities even when
    the string matches — a pool endpoint is not the CLI's own login."""
    backend = _SessionBackend()
    script = _write(tmp_path, "lock.py", _LOCK_SCRIPT.replace("THIRD", third))
    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "fork()" in str(exc.value)
    assert locked_name in str(exc.value)
    assert backend.turns == ["first", "second"]  # same-hint turn ran; the switch never reached the backend


def test_same_hint_every_turn_executes(tmp_path):
    """Re-stating the locked hint (or omitting it) is a no-op lock — all turns run."""
    backend = _SessionBackend()
    script = _write(tmp_path, "same.py", _LOCK_SCRIPT.replace("THIRD", "None"))
    result = asyncio.run(run_workflow(script, backend=backend))
    assert result == ["turn:first", "turn:second", "turn:third"]


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


def test_fork_child_and_parent_lock_independently(tmp_path):
    """A fork child locks its own hint; neither session's lock leaks into the other.

    The child's later turns validate against the child's lock (the error names
    the child's value), and the parent still rejects the child's hint on its
    own turns (the error names the parent's value).
    """
    backend = _SessionBackend()
    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(_write(tmp_path, "fork_lock.py", _FORK_LOCK_SCRIPT), backend=backend))
    assert "deepseek-v4-flash" in str(exc.value)  # the CHILD's locked value
    assert "fork()" in str(exc.value)
    assert backend.turns == ["p1", "c1", "c2", "p2"]  # c3 blocked

    parent_switch = _FORK_LOCK_SCRIPT.replace(
        'await child.send("c3", options={"model": "glm-5"})',
        'await parent.send("p3", options={"model": "deepseek-v4-flash"})',
    )
    backend = _SessionBackend()
    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(_write(tmp_path, "fork_parent_lock.py", parent_switch), backend=backend))
    assert "glm-5" in str(exc.value)  # the PARENT's locked value — untouched by the child's hint
    assert backend.turns == ["p1", "c1", "c2", "p2"]


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


def test_no_hint_lock_names_what_was_actually_locked(tmp_path):
    """A no-hint lock names the real lockee per supply path.

    Native: the backend's describe_base_model names the concrete base spec
    model. Third-party: the engine names the CLI's own default model honestly
    (its concrete name is opaque to the engine).
    """
    backend = _BaseModelNamingBackend()
    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(_write(tmp_path, "concrete_lock.py", _BASE_LOCK_SCRIPT), backend=backend))
    assert "glm-5-air" in str(exc.value)
    assert "fork()" in str(exc.value)
    assert backend.turns == ["first"]

    third_party = _BASE_LOCK_SCRIPT.replace(
        'agent_session(label="chat")', 'agent_session(label="cx", options={"agent_type": "codex"})'
    )
    backend = _SessionBackend()
    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(_write(tmp_path, "cli_default_lock.py", third_party), backend=backend))
    assert "codex CLI's own default model" in str(exc.value)
    assert backend.turns == ["first"]


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


_RETRY_SAME_HINT_SCRIPT = """
from swarmflow import agent_session

META = {"name": "retry-same-hint", "description": "same hint after failed first turn", "phases": []}

async def run(args):
    s = agent_session(label="chat", options={"agent_type": "codex", "model": "glm-5"})
    a = await s.send("first")    # backend rejects -> AGENT_FAILED
    b = await s.send("second")   # same hint (inherits the session lock): no-op, proceeds
    return [a, b]
"""


def test_failed_first_turn_never_consumes_the_lock(tmp_path):
    """A failed first turn never "used" the lock: a corrected hint re-locks and
    the same hint stays on the no-op path — one avatar across all turns."""
    backend = _FlakySessionBackend(fail_prompt="first")
    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(_write(tmp_path, "switch_after_fail.py", _SWITCH_AFTER_FAIL_SCRIPT), backend=backend))
    assert "deepseek-v4-flash" in str(exc.value)  # the RE-LOCKED value, not the failed turn's
    assert "fork()" in str(exc.value)
    assert backend.opened == ["m0"]          # one avatar across all three turns
    assert backend.turns == ["second"]       # first failed, second ran, third blocked

    backend = _FlakySessionBackend(fail_prompt="first")
    result = asyncio.run(run_workflow(_write(tmp_path, "retry_same_hint.py", _RETRY_SAME_HINT_SCRIPT), backend=backend))
    assert result == [None, "turn:second"]
    assert backend.turns == ["second"]


_TEMPLATE_LOCK_SCRIPT = """
from swarmflow import agent_session

META = {"name": "tlock", "description": "session template lock", "phases": []}

async def run(args):
    s = agent_session(label="shot", options={"agent_template": "clip"})
    a = await s.send("first")
    b = await s.send("second", options=SECOND)
    return [a, b]
"""


class _TemplateSessionBackend(_SessionBackend):
    KNOWN_OPTIONS = frozenset({"agent_template"})


def test_session_switching_agent_template_raises_and_points_at_fork(tmp_path):
    backend = _TemplateSessionBackend()
    script = _write(tmp_path, "tlock.py", _TEMPLATE_LOCK_SCRIPT.replace("SECOND", '{"agent_template": "scene"}'))
    with pytest.raises(EngineError) as exc:
        asyncio.run(run_workflow(script, backend=backend))
    assert "agent_template" in str(exc.value) and "'clip'" in str(exc.value) and "fork()" in str(exc.value)
    assert backend.turns == ["first"]


def test_session_restating_or_omitting_agent_template_is_noop(tmp_path):
    backend = _TemplateSessionBackend()
    # Third turn passes no options at all: the session-level template still
    # applies, so omission must not read as "switched to no template".
    script = _write(
        tmp_path,
        "tsame.py",
        '''
from swarmflow import agent_session

META = {"name": "tsame", "description": "session template re-state", "phases": []}

async def run(args):
    s = agent_session(label="shot", options={"agent_template": "clip"})
    a = await s.send("first")
    b = await s.send("second", options={"agent_template": "clip"})
    c = await s.send("third")
    return [a, b, c]
''',
    )
    assert asyncio.run(run_workflow(script, backend=backend)) == ["turn:first", "turn:second", "turn:third"]


def test_fork_child_inherits_agent_template(tmp_path):
    backend = _TemplateSessionBackend()
    script = _write(
        tmp_path,
        "tfork.py",
        '''
from swarmflow import agent_session

META = {"name": "tfork", "description": "fork inherits template", "phases": []}

async def run(args):
    s = agent_session(label="shot", options={"agent_template": "clip"})
    await s.send("first")
    child = await s.fork(label="shot2")
    return await child.send("child turn")
''',
    )
    asyncio.run(run_workflow(script, backend=backend))
    assert backend.opened_opts[-1].get("agent_template") == "clip"
