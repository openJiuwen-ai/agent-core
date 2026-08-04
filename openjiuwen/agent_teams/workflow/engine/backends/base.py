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

    @property
    def budget(self) -> BudgetLedger:
        """The run's token ledger — unbounded until ``run_workflow`` binds one."""
        return self._budget

    def bind_budget(self, budget: BudgetLedger) -> None:
        """Adopt the run's ledger; called once by ``run_workflow`` before the run.

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

    async def open_session(
        self, *, kind: str, instructions: str | None, opts: dict
    ) -> str:
        """Open a stateful session; return its backend-scoped session id.

        ``kind`` is ``"agent"`` (LLM-driven) or ``"human"`` (each turn's input
        comes from a real person); the engine forwards it opaquely. The default
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
