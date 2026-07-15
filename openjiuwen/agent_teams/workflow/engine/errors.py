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
    """The agent backend raised while producing a result."""


class WorkflowAborted(BaseException):
    """Cooperative pause signal raised at an ``agent()`` abort checkpoint.

    A ``BaseException`` (not ``EngineError`` / ``Exception``) so it propagates
    through ``parallel()`` / ``pipeline()`` branch bodies' ``except Exception``
    exactly like ``CancelledError`` — the in-flight call neither journals its
    result nor maps to ``None``; the run unwinds so a later resume reruns it.
    """
