# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workflow engine exceptions.

These stay internal to ``workflow/engine``. The public ``swarmflow()`` tool
boundary converts them to the repo-wide ``StatusCode`` system; engine code and
ported workflow scripts never see the team error taxonomy.
"""
from __future__ import annotations


class WorkflowError(Exception):
    """Base class for all workflow-engine errors."""


class MetaError(WorkflowError):
    """The ``META = {...}`` block is missing or not a pure literal."""


class LintError(WorkflowError):
    """A determinism/closure lint rule failed in strict mode."""


class SchemaError(WorkflowError):
    """A schema argument could not be resolved (not a model / dict / None)."""


class BackendError(WorkflowError):
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
    """Cooperative pause signal raised at an ``agent()`` abort checkpoint.

    A ``BaseException`` (not ``WorkflowError`` / ``Exception``) so it propagates
    through ``parallel()`` / ``pipeline()`` branch bodies' ``except Exception``
    exactly like ``CancelledError`` — the in-flight call neither journals its
    result nor maps to ``None``; the run unwinds so a later resume reruns it.
    """


class BudgetExhausted(BaseException):
    """The run hit its token ceiling at an ``agent()`` / ``send()`` budget gate.

    A ``BaseException`` for the same reason as :class:`WorkflowAborted`: a
    ceiling a script can swallow with ``except Exception`` (and keep spawning
    agents from) is not a ceiling. Scripts that want to finish gracefully poll
    ``budget.remaining()`` and stop on their own; this is the backstop for the
    ones that do not.

    Unlike an abort, exhaustion is terminal rather than resumable — no resume
    reruns the blocked call, so the run's completed prefix stays journalled and
    the exception surfaces as a run failure.
    """
