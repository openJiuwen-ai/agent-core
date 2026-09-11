# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent backend interface.

A backend is the *only* place real non-determinism / IO lives. The engine
hands it a fully-rendered prompt, the call's ``opts``, and (when the call
requested structured output) the JSON-Schema dict; it returns an
:class:`AgentResult`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Sequence

from ..budget import BudgetLedger


@dataclass
class AgentResult:
    """What a backend returns for one ``agent()`` / session turn.

    * ``text``       - free text, when no schema was requested.
    * ``structured`` - a JSON-able object conforming to the schema, when one was.
    * ``tokens``     - tokens this one call consumed, for reporting. The engine
      does **not** accumulate it — see :meth:`AgentBackend.bind_budget`.
    * ``skipped``    - the backend declined to answer; the call returns ``None``
      (also how a human turn signals a timeout / no answer).
    """

    text: str | None = None
    structured: Any = None
    tokens: int = 0
    skipped: bool = False


class AgentBackend(abc.ABC):
    """Pluggable agent executor.

    The single-shot ``run`` powers ``agent()``. The optional stateful-session
    quartet (``open_session`` / ``send_turn`` / ``close_session`` / ``aclose``)
    powers the multi-turn ``agent_session()`` / ``human_session()`` primitives;
    a backend that only does single-shot work leaves them at their defaults and
    the session primitives raise a clear error against it.
    """

    #: Extra ``options``-bag keys this backend accepts beyond the engine's own
    #: (``label`` / ``phase`` / ``schema`` / ``model`` / ``timeout``). The engine
    #: validates each session primitive's ``options`` against
    #: ``_ENGINE_OPTIONS | backend.KNOWN_OPTIONS`` and fails fast on anything
    #: else, so a typo never silently no-ops. Empty by default.
    KNOWN_OPTIONS: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self._budget = BudgetLedger()
        self._workflow_budget: BudgetLedger | None = None

    @property
    def budget(self) -> BudgetLedger:
        """The run's session-wide token ledger — unbounded until ``run_workflow`` binds one."""
        return self._budget

    @property
    def workflow_budget(self) -> BudgetLedger | None:
        """The run's per-run token ledger (resets each ``swarmflow`` invocation).

        ``None`` for backends that do not participate in per-run accounting
        (older implementations); the engine's own ``_check_budget`` gate still
        reads ``rt.workflow_budget`` directly regardless of this binding.
        """
        return self._workflow_budget

    def bind_budget(self, budget: BudgetLedger) -> None:
        """Adopt the run's session-wide ledger; called once by ``run_workflow`` before the run.

        **The backend is the ledger's only writer.** It is the only layer that
        knows what a call really cost: one ``agent()`` is a whole agent loop, so
        the engine — which sees only the call's start and end — cannot account
        for it, and used to guess (prompt length / 4) instead. A backend reports
        real usage as each model call returns, which is also what makes the
        ceiling enforceable mid-loop rather than only between ``agent()`` calls.

        Overriding is only needed to fan the ledger out further (e.g. into rails
        the backend attaches to the agents it spawns); call ``super()`` first.
        """
        self._budget = budget

    def bind_workflow_budget(self, workflow_budget: BudgetLedger) -> None:
        """Adopt the run's per-run ledger (companion to :meth:`bind_budget`).

        Bound by ``run_workflow`` alongside the session ledger. The per-run
        ledger resets to ``spent=0`` on each new ``swarmflow`` invocation and
        caps a single run independently of the session budget. Backends fan it
        out into the same rails that bill the session ledger (so every model
        call is reported to both); overriding is only needed to fan out further.
        """
        self._workflow_budget = workflow_budget

    @abc.abstractmethod
    async def run(
        self, prompt: str, opts: dict, schema_json: dict | None
    ) -> AgentResult:
        """Execute one single-shot agent call.

        ``schema_json`` is the JSON-Schema dict when structured output was
        requested (pydantic models are already lowered to JSON Schema by the
        engine), else ``None``.
        """
        raise NotImplementedError

    async def capture_fork(self, session_id: str, *, keep_rounds: int | None, fork_mode: str) -> dict | None:
        """Eagerly snapshot a session's context per ``fork_mode`` / ``keep_rounds``.

        Called at ``AgentSession.fork()`` time so the parent's context is frozen
        at the fork point (a later lazy capture would pick up the parent's own
        evolution). Returns a serializable ``fork_data`` dict (``messages`` + the
        compact split/direction when a compact mode was requested) for injection
        into a fresh child session, or ``None`` when the session has no
        captureable context — the caller then falls back to the engine's history
        mirror (degraded, no ToolMessage).

        The default rejects forking so a session-less / single-shot-only backend
        fails clearly rather than silently producing an empty fork.
        """
        raise NotImplementedError("backend does not support forking sessions")

    async def ensure_member_name(self, *, kind: str, opts: dict) -> str:
        """Reserve this session's member identity without building its avatar.

        Called on a session's **first turn regardless of cache hit** so the
        engine knows the session's stable member name even when every turn is a
        journal replay (no avatar is ever built). This name is what ``fork()``
        needs to locate the parent's persisted context after a fully-hit resume
        — without it, a parent that never re-ran could not be restored.

        Unlike :meth:`open_session` this must NOT build a harness, call an LLM,
        or hold a spawn/budget slot — it is pure in-process bookkeeping (a
        counter increment + name mint). The default rejects so a single-shot-only
        backend fails clearly; a session-capable backend implements it.
        """
        raise NotImplementedError("backend does not support stateful sessions")

    async def open_session(
        self,
        *,
        kind: str,
        instructions: str | None,
        opts: dict,
        fork_data: dict | None = None,
        member_name: str | None = None,
    ) -> str:
        """Open a stateful session; return its backend-scoped session id.

        ``kind`` is ``"agent"`` (LLM-driven) or ``"human"`` (each turn's input
        comes from a real person); the engine forwards it opaquely.
        ``fork_data`` is an optional context snapshot captured by
        :meth:`capture_fork`; a backend that supports forking seeds the new
        session's context from it (and compacts when requested). ``member_name``
        is the identity already reserved by :meth:`ensure_member_name` on the
        first turn — a backend that tracks names reuses it (rather than minting a
        fresh one, which would drift the counter across a resume). The default
        rejects sessions so a single-shot-only backend fails clearly.
        """
        raise NotImplementedError("backend does not support stateful sessions")

    async def send_turn(
        self,
        session_id: str,
        prompt: str,
        opts: dict,
        schema_json: dict | None,
        *,
        history: Sequence[dict] = (),
        correlation_id: str | None = None,
    ) -> AgentResult:
        """Advance one turn on an open session and return its result.

        ``history`` is the engine-side conversation so far
        (``[{"role", "content"}, ...]``). A live backend that keeps its own
        session state uses it only to rebuild context once after a resume; in the
        normal path it is redundant with the backend's own state.

        ``correlation_id`` is a deterministic id for a human turn
        (``{phase}:{label}:{turn}``, set by the engine) used to match a person's
        reply back to this turn; ``None`` for agent turns. Being deterministic
        (not a uuid) it stays valid across a resume.
        """
        raise NotImplementedError("backend does not support stateful sessions")

    async def close_session(self, session_id: str) -> None:
        """Close one session and release its resources (idempotent)."""
        raise NotImplementedError("backend does not support stateful sessions")

    async def aclose(self) -> None:
        """Release all backend resources at run end (close any open sessions).

        Called by ``run_workflow`` in a ``finally``; the default is a no-op so
        single-shot backends need not implement it.
        """
        return None
