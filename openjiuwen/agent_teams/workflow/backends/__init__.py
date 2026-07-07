# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarmflow backends: the engine execution layer + the team integration seam.

Re-exports the engine's business-agnostic ``AgentBackend`` / ``AgentResult`` /
``MockBackend`` alongside the team-specific ``TeamWorkerBackend`` (maps engine
``agent()`` calls onto single-shot WORKER members) and ``StructuredOutputTool``
(the structured-output tool a worker calls to return its result).
"""
from __future__ import annotations

from openjiuwen.agent_teams.workflow.engine.backends.base import AgentBackend, AgentResult
from openjiuwen.agent_teams.workflow.engine.backends.mock import MockBackend
from openjiuwen.agent_teams.tools.structured_output_tool import StructuredOutputTool
from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend

__all__ = [
    "AgentBackend",
    "AgentResult",
    "MockBackend",
    "StructuredOutputTool",
    "TeamWorkerBackend",
]
