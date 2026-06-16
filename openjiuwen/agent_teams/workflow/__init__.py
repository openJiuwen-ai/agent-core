# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SwarmFlow workflow orchestration for agent teams.

A swarmflow script (an ordinary Python module with ``META = {...}`` and
``async def run(args)``) declaratively orchestrates a multi-agent workflow on
top of the agent_teams infrastructure.

Layering:

* ``engine/`` — a faithful, business-agnostic port of the dw/wf deterministic
  engine (facade → seam → provider → primitives → ``AgentBackend``). Unit-tested
  in isolation with ``MockBackend``.
* ``backends/`` — the team integration backend (``TeamWorkerBackend``) that maps
  each engine ``agent()`` call onto a single-shot ``WORKER`` member, plus the
  structured-output tool (``StructuredOutputTool``) the worker must call.
* ``observer`` / ``schema`` / ``runner`` — the progress observer, the 4-layer
  ``WorkflowRun`` model, and the ``run_swarmflow`` / ``preprocess_swarmflow``
  entrypoints.

The leader-facing ``swarmflow()`` tool and the spectator-broadcast wiring live
alongside and are added in later build phases.
"""
from __future__ import annotations

from openjiuwen.agent_teams.workflow.backends import (
    AgentBackend,
    AgentResult,
    MockBackend,
    StructuredOutputTool,
    TeamWorkerBackend,
)
from openjiuwen.agent_teams.workflow.engine import PhasePlan, WorkflowProgressEvent, run_workflow
from openjiuwen.agent_teams.workflow.observer import WorkflowObserver
from openjiuwen.agent_teams.workflow.runner import preprocess_swarmflow, run_swarmflow
from openjiuwen.agent_teams.workflow.schema import (
    AgentActivity,
    PhaseRecord,
    WorkflowRun,
    build_workflow_run_from_events,
)

__all__ = [
    # entrypoints
    "run_swarmflow",
    "preprocess_swarmflow",
    "run_workflow",
    # types
    "PhasePlan",
    # backends
    "AgentBackend",
    "AgentResult",
    "MockBackend",
    "TeamWorkerBackend",
    "StructuredOutputTool",
    # observability + 4-layer model
    "WorkflowObserver",
    "WorkflowProgressEvent",
    "WorkflowRun",
    "PhaseRecord",
    "AgentActivity",
    "build_workflow_run_from_events",
]
