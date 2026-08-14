# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metis task-memory preview operator for self-evolution.

The operator owns only ``updates_generated -> local_apply_completed``.
Real state lives in ``MetisMemoryStore``; the rail commits previewed
deltas after ``execute_updates``.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.protocols import APPEND_MODE, STATE_EFFECT, TASK_MEMORY_TARGET
from openjiuwen.core.operator.context_evolve_call.base import ContextEvolveOperator, UpdatePolicy


class MetisContextEvolveOperator(ContextEvolveOperator):
    """Preview-only parameter handle for the Metis task-memory library."""

    operator_id_prefix = "metis_context_evolve_"
    update_policies = {
        TASK_MEMORY_TARGET: UpdatePolicy(
            allowed=frozenset({(APPEND_MODE, STATE_EFFECT)}),
            kind="task_memory",
            path="library",
            constraint={"type": "delta"},
        )
    }


__all__ = ["MetisContextEvolveOperator"]
