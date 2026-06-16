# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""workflow.engine — the **internal** reference engine behind SwarmFlow.

A faithful, business-agnostic port of the dw/wf deterministic asyncio engine.
It is *not* the public surface: workflow scripts import from the facade (mapped
to the bare name ``swarmflow`` at runtime), and the engine installs itself as
the active *provider* (see :mod:`workflow.engine.provider` /
:mod:`workflow.engine.seam`) when you call ``run_workflow``. Swapping engines
means installing a different provider; scripts never name this package.

The team integration (``TeamWorkerBackend``, the observer, the ``swarmflow()``
tool) lives in the parent ``workflow`` package — this engine subpackage stays
free of any agent_teams import so it can be unit-tested in isolation with the
``MockBackend``.
"""
from __future__ import annotations

from .backends import SKIP, AgentBackend, AgentResult, MockBackend
from .errors import LintError, MetaError, SchemaError, WorkflowError
from .journal import Journal
from .loader import LoadedWorkflow, load_workflow_source
from .primitives import (
    agent,
    budget,
    compact,
    flatten_filter,
    log,
    map_parallel,
    parallel,
    phase,
    pipeline,
    pmap,
    workflow,
)
from .progress import (
    PhasePlan,
    ProgressKind,
    ProgressSink,
    WorkflowProgressEvent,
    noop_progress_sink,
)
from .runner import run_workflow
from .runtime import Runtime

__all__ = [
    # engine
    "run_workflow",
    "Runtime",
    "Journal",
    "load_workflow_source",
    "LoadedWorkflow",
    # injected/importable DSL primitives
    "agent",
    "parallel",
    "pipeline",
    "map_parallel",
    "pmap",
    "phase",
    "log",
    "workflow",
    "budget",
    "compact",
    "flatten_filter",
    # backends
    "AgentBackend",
    "AgentResult",
    "MockBackend",
    "SKIP",
    # progress observability
    "PhasePlan",
    "ProgressKind",
    "ProgressSink",
    "WorkflowProgressEvent",
    "noop_progress_sink",
    # errors
    "WorkflowError",
    "MetaError",
    "LintError",
    "SchemaError",
]
