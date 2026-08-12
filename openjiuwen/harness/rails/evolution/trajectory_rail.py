# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Synchronous execution trajectory recorder rail."""

from __future__ import annotations

import threading
from typing import Any

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.spans import merge_trajectories, span_identity
from openjiuwen.agent_evolving.trajectory.store import TrajectoryStore
from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.evolution.evolution_rail import (
    EvolutionRail,
    EvolutionTriggerPoint,
    _InvokeCapture,
)


class TrajectoryRail(EvolutionRail):
    """Record one canonical execution archive per Agent invoke.

    The recorder accumulator is intentionally separate from the inherited
    clean evolution window.  It receives every normalizable increment,
    including increments accompanied by capture-quality issues, and writes at
    most once synchronously during the final invoke hook.
    """

    priority = 10

    def __init__(
        self,
        *,
        trajectory_span_processor: TrajectorySpanProcessor,
        trajectory_store: TrajectoryStore,
        max_trajectory_spans: int | None = 200,
    ) -> None:
        super().__init__(
            max_trajectory_spans=max_trajectory_spans,
            evolution_trigger=EvolutionTriggerPoint.NONE,
            trajectory_span_processor=trajectory_span_processor,
        )
        if trajectory_store is None:
            raise TypeError("trajectory_store is required")
        self._trajectory_store = trajectory_store
        self._execution_accumulators: dict[object, Trajectory] = {}
        self._execution_seen_spans: set[tuple[str, str]] = set()
        self._execution_lock = threading.RLock()

    @property
    def trajectory_store(self) -> TrajectoryStore:
        """Return the explicitly configured synchronous archive."""

        return self._trajectory_store

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await super().before_invoke(ctx)
        capture = self._current_capture()
        if capture is not None:
            with self._execution_lock:
                self._execution_accumulators.pop(capture.subscription, None)

    def _record_execution_increment(
        self,
        capture: _InvokeCapture,
        increment: Trajectory,
    ) -> None:
        with self._execution_lock:
            increment = self._select_new_execution_spans(increment)
            if increment is None:
                return
            previous = self._execution_accumulators.get(capture.subscription)
            self._execution_accumulators[capture.subscription] = (
                merge_trajectories(previous, increment) if previous is not None else increment
            )

    def _select_new_execution_spans(self, increment: Trajectory) -> Trajectory | None:
        """Remove identities already archived by this recorder stream.

        Callers hold ``_execution_lock`` so checking and reserving identities is
        atomic across concurrent Agent invokes or Team runs.
        """

        payload = increment.to_otlp()
        selected = False
        for resource_span in payload.get("resourceSpans") or []:
            for scope_span in resource_span.get("scopeSpans") or []:
                spans = scope_span.get("spans") or []
                scope_span["spans"] = []
                for span in spans:
                    identity = span_identity(span)
                    if identity is not None and identity in self._execution_seen_spans:
                        continue
                    if identity is not None:
                        self._execution_seen_spans.add(identity)
                    scope_span["spans"].append(span)
                    selected = True
        return Trajectory.from_otlp(payload) if selected else None

    def _save_execution_archive(self, archive: Trajectory, capture: _InvokeCapture) -> None:
        """Apply canonical metadata and synchronously save one archive."""

        archive = self._with_scope_metadata(archive, self._scope_metadata(capture))
        try:
            self._trajectory_store.save(archive)
        except Exception as exc:  # pragma: no cover - backend-specific errors
            logger.warning("[%s] failed to save execution trajectory: %s", type(self).__name__, exc)

    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        del ctx, trajectory
        capture = self._current_capture()
        if capture is None:
            return
        with self._execution_lock:
            archive = self._execution_accumulators.pop(capture.subscription, None)
        if archive is None:
            return
        # Store.save is intentionally synchronous and at-most-once.  A backend
        # failure is isolated from the callback/evolution flow.
        self._save_execution_archive(archive, capture)

    def uninit(self, agent: Any) -> None:
        with self._execution_lock:
            self._execution_accumulators.clear()
            self._execution_seen_spans.clear()
        super().uninit(agent)


__all__ = ["TrajectoryRail"]
