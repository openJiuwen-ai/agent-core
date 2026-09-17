# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workflow engine exceptions.

These stay internal to ``workflow/engine``. The public ``swarmflow()`` tool
boundary converts them to the repo-wide ``StatusCode`` system; engine code and
ported workflow scripts never see the team error taxonomy.
"""
from __future__ import annotations


class EngineError(Exception):
    """Base class for all workflow-engine errors.

    Named ``EngineError`` (not ``WorkflowError``) to avoid colliding with
    ``openjiuwen.core.common.exception.errors.WorkflowError`` — the two are
    unrelated and must not be confusable at except-sites.
    """


class MetaError(EngineError):
    """The ``META = {...}`` block is missing or not a pure literal."""


class LintError(EngineError):
    """A determinism/closure lint rule failed in strict mode."""


class SchemaError(EngineError):
    """A schema argument could not be resolved (not a model / dict / None)."""


class BackendError(EngineError):
    """The agent backend raised while producing a result.

    Carries the tokens the failed call actually burned (``budget_rail``.
    ``call_tokens``), so a failed/budget-exhausted agent's consumption is not
    silently dropped from the AGENT_FAILED event and the UI's "Run tokens"
    stays consistent with "Team budget" (the shared ledger that already
    counted those tokens). ``None`` when the backend could not attribute
    tokens (e.g. the failure happened before any model call).
    """

    def __init__(self, message: str, *, tokens: int | None = None) -> None:
        super().__init__(message)
        self.tokens = tokens


class WorkflowAborted(BaseException):
    """Cooperative control signal raised at an abort checkpoint.

    Carries a ``reason`` distinguishing the three control intents (early_return
    / pause / stop), plus an optional payload (reply + edit_hints) for the
    early-return path so SwarmflowTool can inject them into the leader's next
    turn. A bare ``WorkflowAborted()`` defaults to ``reason="pause"`` (back-compat
    with the controller pause path).

    A ``BaseException`` (not ``EngineError`` / ``Exception``) so it propagates
    through ``parallel()`` / ``pipeline()`` branch bodies' ``except Exception``
    exactly like ``CancelledError`` — the in-flight call neither journals its
    result nor maps to ``None``; the run unwinds so a later resume reruns it.
    """

    def __init__(
        self,
        *,
        reason: str = "pause",
        reply: str | None = None,
        edit_hints: str | None = None,
    ) -> None:
        super().__init__("workflow aborted")
        self.reason = reason
        self.reply = reply
        self.edit_hints = edit_hints


class BudgetExhausted(BaseException):
    """The run hit a token ceiling at an ``agent()`` / ``send()`` budget gate.

    Two distinct ceilings share this type, distinguished by ``scope``:

    - ``scope="workflow"``: the **per-run** ledger (``rt.workflow_budget``) is
      exhausted. It resets to ``spent=0`` on each new ``swarmflow`` invocation,
      so this is **retryable by revising the workflow** and relaunching.
    - ``scope="session"``: the **team-wide** ledger (``rt.budget``) is
      exhausted. It is shared across every run and never resets, so relaunching
      only hits the same gate.

    A ``BaseException`` for the same reason as :class:`WorkflowAborted`: a
    ceiling a script can swallow with ``except Exception`` (and keep spawning
    agents from) is not a ceiling. Scripts that want to finish gracefully poll
    ``budget.remaining()`` and stop on their own; this is the backstop for the
    ones that do not.

    Unlike an abort, exhaustion is terminal rather than resumable — no resume
    reruns the blocked call, so the run's completed prefix stays journalled and
    the exception surfaces as a run failure.
    """

    def __init__(self, message: str, *, scope: str = "session",
                 spent: int | None = None, total: int | None = None,
                 top_phases: list[tuple[str, int]] | None = None,
                 workflow_spent: int | None = None,
                 workflow_total: int | None = None) -> None:
        super().__init__(message)
        self.scope = scope   # "workflow" | "session"
        self.spent = spent
        self.total = total
        self.top_phases = top_phases             # top-3 phases by consumption: list[(phase, tokens)]
        self.workflow_spent = workflow_spent     # workflow-level spent at exhaustion (contrast for session)
        self.workflow_total = workflow_total     # workflow-level total at exhaustion
