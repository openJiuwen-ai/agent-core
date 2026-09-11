# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metis assembly of the context-evolve rail.

Wires the Metis four-piece set (query service / store / optimizer / operator)
into :class:`ContextEvolveRail` and maps snapshot facts to the Metis signal
fields. All orchestration lives in the base template.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis import (
    MetisMemoryStore,
    MetisQueryService,
    MetisReflectorLLM,
)
from openjiuwen.agent_evolving.optimizer.context_evolve_call.metis_optimizer import MetisContextEvolveOptimizer
from openjiuwen.agent_evolving.protocols import TASK_MEMORY_TARGET
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.operator.context_evolve_call.metis import MetisContextEvolveOperator
from openjiuwen.harness.rails.evolution.context_evolve_rail import ContextEvolveRail, OutcomeResolver
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionTriggerPoint

_MEMORY_SECTION_NAME = "metis_task_memory"
_MEMORY_SECTION_PRIORITY = 87  # right after the evolution protocol section (86)


class MetisContextEvolveRail(ContextEvolveRail):
    """Online read/write driver for Metis workspace task memory."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        llm: Model,
        model: str,
        trajectory_span_processor: TrajectorySpanProcessor,
        user_id: str,
        store: Optional[MetisMemoryStore] = None,
        persist_dir: Optional[str] = "./memories/metis",
        threshold: int = 3,
        executor_context: str = "",
        inject_memories: bool = True,
        auto_evolve: bool = True,
        outcome_resolver: Optional[OutcomeResolver] = None,
        evolution_trigger: EvolutionTriggerPoint = EvolutionTriggerPoint.AFTER_INVOKE,
        async_evolution: bool = True,
        max_concurrent_evolution: int = 1,
    ) -> None:
        """Initialize Metis task-memory retrieval and evolution.

        Args:
            llm: Model client used by the Manager and reflectors.
            model: Model name passed to the client.
            trajectory_span_processor: Shared trajectory capture processor.
            user_id: Required identity and memory-isolation scope.
            store: Optional preconfigured Metis memory store.
            persist_dir: Snapshot directory used when creating the store.
                Use ``None`` for in-memory storage.
            threshold: Successful plan uses required before codification.
            executor_context: Environment knowledge excluded from new memories.
            inject_memories: Whether selected memories enter the system prompt.
            auto_evolve: Whether finished tasks update the memory library.
            outcome_resolver: Optional task-result normalization callback.
            evolution_trigger: Lifecycle point that starts evolution.
            async_evolution: Whether evolution runs in the background.
            max_concurrent_evolution: Maximum concurrent evolution runs.

        """
        store = store or MetisMemoryStore(persist_dir=persist_dir)
        super().__init__(
            retriever=MetisQueryService(
                store=store,
                llm=MetisReflectorLLM(llm, model),
            ),
            store=store,
            optimizer=MetisContextEvolveOptimizer(
                llm,
                model,
                threshold=threshold,
                executor_context=executor_context,
            ),
            operator=MetisContextEvolveOperator(user_id),
            targets=[TASK_MEMORY_TARGET],
            scope_id=user_id,
            section_name=_MEMORY_SECTION_NAME,
            section_priority=_MEMORY_SECTION_PRIORITY,
            outcome_resolver=outcome_resolver,
            inject_context=inject_memories,
            auto_evolve=auto_evolve,
            trajectory_span_processor=trajectory_span_processor,
            evolution_trigger=evolution_trigger,
            async_evolution=async_evolution,
            max_concurrent_evolution=max_concurrent_evolution,
        )

    def build_signal_context(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        """Map captured task facts to Metis-specific signal fields."""
        evolution_context = facts.get("evolution_context") or {}
        return {
            "query": facts.get("query") or "",
            "outcome": facts.get("outcome") or "Unknown",
            "selected_tip_ids": list(evolution_context.get("selected_tip_ids") or []),
        }


__all__ = ["MetisContextEvolveRail"]
