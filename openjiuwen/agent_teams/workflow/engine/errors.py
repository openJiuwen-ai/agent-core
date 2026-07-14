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
    """The agent backend raised while producing a result."""


class WorkflowAborted(BaseException):
    """Cooperative pause signal raised at an ``agent()`` abort checkpoint.

    A ``BaseException`` (not ``WorkflowError`` / ``Exception``) so it propagates
    through ``parallel()`` / ``pipeline()`` branch bodies' ``except Exception``
    exactly like ``CancelledError`` — the in-flight call neither journals its
    result nor maps to ``None``; the run unwinds so a later resume reruns it.
    """
