# coding: utf-8
"""Canonical trajectory-to-GraphEngine integration boundary."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Protocol

from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.harness.rails.evolution.evolution_rail import (
    EvolutionRail,
    EvolutionTriggerPoint,
    PreparedEvolutionInput,
)
from openjiuwen.symphony.observation import GraphEvolutionInput, ObservationReceipt

GraphEvolutionInputBuilder = Callable[
    [PreparedEvolutionInput],
    GraphEvolutionInput | None | Awaitable[GraphEvolutionInput | None],
]


class GraphObservationSink(Protocol):
    """Minimal GraphEngine surface consumed by the Rail."""

    def submit_observation(self, value: GraphEvolutionInput) -> ObservationReceipt:
        """Append one canonical graph observation."""


class SymphonyGraphEvolutionRail(EvolutionRail):
    """Convert canonical trajectories and submit qualified graph evidence.

    The input builder belongs to the trajectory/evaluator integration layer. It
    must produce ``GraphEvolutionInput`` and is the only component that knows
    how task outcomes and span evidence are resolved. GraphEngine never reads
    Session JSON or guesses an edge failure from an incomplete trajectory.
    """

    def __init__(
        self,
        *,
        trajectory_span_processor: TrajectorySpanProcessor,
        graph_engine: GraphObservationSink,
        input_builder: GraphEvolutionInputBuilder,
        async_evolution: bool = True,
        max_trajectory_spans: int | None = 200,
    ) -> None:
        super().__init__(
            trajectory_span_processor=trajectory_span_processor,
            evolution_trigger=EvolutionTriggerPoint.AFTER_INVOKE,
            async_evolution=async_evolution,
            max_concurrent_evolution=1,
            max_trajectory_spans=max_trajectory_spans,
        )
        if graph_engine is None or not callable(getattr(graph_engine, "submit_observation", None)):
            raise TypeError("graph_engine must provide submit_observation")
        if not callable(input_builder):
            raise TypeError("input_builder must be callable")
        self._graph_engine = graph_engine
        self._input_builder = input_builder

    async def run_evolution(self, prepared: PreparedEvolutionInput) -> None:
        value = self._input_builder(prepared)
        if inspect.isawaitable(value):
            value = await value
        if value is None:
            return
        if not isinstance(value, GraphEvolutionInput):
            raise TypeError("input_builder must return GraphEvolutionInput or None")
        self._graph_engine.submit_observation(value)


__all__ = [
    "GraphEvolutionInputBuilder",
    "GraphObservationSink",
    "SymphonyGraphEvolutionRail",
]
