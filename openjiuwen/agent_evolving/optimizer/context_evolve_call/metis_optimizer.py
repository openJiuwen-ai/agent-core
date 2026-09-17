# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context-evolve optimizer running the Metis evolve pass.

Consumes one ``task_completed`` signal (task facts in ``signal.context``) plus
the per-scope library snapshot passed through ``bind(scope_states=...)``, runs
``evolve_after_task``, and emits a structured ``UpdateValue`` whose payload is
a :class:`MetisMemoryDelta`. Persistence stays outside: the rail commits the
delta to the ``MetisMemoryStore`` after ``execute_updates``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from openjiuwen.agent_evolving.optimizer.context_evolve_call.base import ContextEvolveOptimizerBase
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis import (
    METIS_ALGORITHM_ID,
    METIS_LLM_POLICY,
    MetisMemoryDelta,
    MetisReflectorLLM,
    evolve_after_task,
    render_trajectory_text,
)
from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy
from openjiuwen.agent_evolving.protocols import (
    APPEND_MODE,
    STATE_EFFECT,
    TASK_MEMORY_ENTRY,
    TASK_MEMORY_TARGET,
)
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.types import UpdateValue
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model


class MetisContextEvolveOptimizer(ContextEvolveOptimizerBase):
    """Metis task-memory evolution as a context-dimension optimizer."""

    algorithm_id = METIS_ALGORITHM_ID

    def __init__(
        self,
        llm: Model,
        model: str,
        *,
        threshold: int = 3,
        executor_context: str = "",
        llm_policy: LLMInvokePolicy = METIS_LLM_POLICY,
    ) -> None:
        """Initialize the Metis evolution algorithm.

        Args:
            llm: Model client used for Manager and reflection calls.
            model: Model name passed to the client.
            threshold: Successful plan uses required before code codification.
            executor_context: Environment knowledge excluded from new memories.
            llm_policy: Timeout and retry policy for Metis LLM calls.

        """
        super().__init__()
        self._reflector = MetisReflectorLLM(llm, model, policy=llm_policy)
        self._threshold = threshold
        self._executor_context = executor_context

    @staticmethod
    def default_targets() -> List[str]:
        """Return the task-memory target owned by this optimizer."""
        return [TASK_MEMORY_TARGET]

    async def _evolve(  # pylint: disable=too-many-locals
        self,
        trajectories: List[Trajectory],
        signals: List[EvolutionSignal],
    ) -> None:
        signal = signals[0]
        ctx = signal.context or {}
        task_id = str(ctx.get("task_id") or "task")
        query = str(ctx.get("query") or signal.excerpt or "")
        outcome = str(ctx.get("outcome") or "Unknown")
        selected_tip_ids = [str(i) for i in ctx.get("selected_tip_ids") or []]

        trajectory_text = str(ctx.get("trajectory_text") or "")
        if not trajectory_text:
            trajectory_text = render_trajectory_text(trajectories[-1]) if trajectories else ""

        for op_id, operator in self._operators.items():
            scope_id = str(getattr(operator, "scope_id", "") or "")
            state = self.scope_states.get(scope_id)
            if state is None:
                raise build_error(
                    StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                    error_msg=(
                        f"scope_states missing entry for scope {scope_id!r}; "
                        "MetisContextEvolveOptimizer requires the library snapshot from MetisMemoryStore.load_state"
                    ),
                )
            before_tip_ids = {t.id for t in state.tips}
            before_tool_ids = {t.id for t in state.tools}

            await evolve_after_task(
                self._reflector,
                task_id=task_id,
                query=query,
                trajectory=trajectory_text,
                selected_tip_ids=selected_tip_ids,
                state=state,
                threshold=self._threshold,
                outcome=outcome,
                executor_context=self._executor_context,
            )

            delta = MetisMemoryDelta(
                user_id=scope_id,
                task_id=task_id,
                state=state,
                new_tip_ids=[t.id for t in state.tips if t.id not in before_tip_ids],
                new_tool_ids=[t.id for t in state.tools if t.id not in before_tool_ids],
            )
            self._parameters[op_id].set_gradient(TASK_MEMORY_TARGET, delta)
            logger.info(
                "[MetisContextEvolveOptimizer] evolved scope=%s task=%s: +%d tips, +%d tools",
                scope_id,
                task_id,
                len(delta.new_tip_ids),
                len(delta.new_tool_ids),
            )

    def _step(self) -> Dict[tuple[str, str], Any]:
        updates: Dict[tuple[str, str], Any] = {}
        for op_id, param in self._parameters.items():
            delta = param.get_gradient(TASK_MEMORY_TARGET)
            if delta is None:
                continue
            updates[(op_id, TASK_MEMORY_TARGET)] = UpdateValue(
                payload=delta,
                mode=APPEND_MODE,
                effect=STATE_EFFECT,
                change_type=TASK_MEMORY_ENTRY,
                metadata={"scope_id": delta.user_id, "task_id": delta.task_id},
            )
        return updates


__all__ = ["MetisContextEvolveOptimizer"]
