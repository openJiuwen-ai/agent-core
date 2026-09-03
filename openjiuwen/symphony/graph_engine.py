"""Unified public facade for Symphony graph capabilities."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Callable

from openjiuwen.symphony.orchestration import (
    CapabilityGraph,
    GraphArtifactStatus,
    GraphBuildResult,
    GraphMutationResult,
    OrchestrationPlan,
    OrchestrationProgress,
    OrchestrationService,
    PrepareArtifactHook,
)
from openjiuwen.symphony.observation import GraphEvolutionInput, GraphSnapshot, ObservationReceipt
from openjiuwen.symphony.observation.service import GraphObservationService

ProgressCallback = Callable[[OrchestrationProgress], Any]
LOGGER = logging.getLogger(__name__)


class SymphonyGraphEngine:
    """Expose graph lifecycle operations while keeping services internal.

    ``OrchestrationService`` remains the implementation of the current static
    graph lifecycle.  The facade is the stable integration point where static
    and future observation-layer operations can share revision semantics.
    """

    def __init__(self, orchestration_service: OrchestrationService) -> None:
        self._orchestration_service = orchestration_service
        self._observation_service = GraphObservationService(orchestration_service.graph_artifact_root)

    def status(self, *, expected_snapshot: dict[str, Any] | None = None) -> GraphArtifactStatus:
        return self._orchestration_service.status(expected_snapshot=expected_snapshot)

    def read(self, version: str | None = None) -> CapabilityGraph:
        return self._orchestration_service.read(version)

    async def add_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        result = await self._orchestration_service.apply_skill_graph_mutation(
            "add",
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )
        await self._reproject_observations()
        return result

    async def update_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        result = await self._orchestration_service.apply_skill_graph_mutation(
            "update",
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )
        await self._reproject_observations()
        return result

    async def delete_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        result = await self._orchestration_service.apply_skill_graph_mutation(
            "delete",
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )
        await self._reproject_observations()
        return result

    async def build(
        self,
        force: bool = False,
        *,
        progress: ProgressCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
    ) -> GraphBuildResult:
        result = await self._orchestration_service.build(
            force,
            progress=progress,
            progress_callback=progress_callback,
            prepare_artifact=prepare_artifact,
        )
        await self._reproject_observations()
        return result

    async def cancel_build(self) -> GraphArtifactStatus:
        return await self._orchestration_service.cancel_build()

    async def plan(
        self,
        query: str,
        candidate_ids: Sequence[str] | None = None,
        *,
        language: str = "cn",
        progress: ProgressCallback | None = None,
        disabled_capability_ids: Sequence[str] | None = None,
        graph_scope_id: str = "default",
        merged_revision: str | None = None,
        task_cluster_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
        mode: str | None = None,
    ) -> OrchestrationPlan:
        snapshot = None
        if self._orchestration_service.config.dynamic_graph_enabled:
            try:
                snapshot = await asyncio.to_thread(
                    self._observation_service.get_snapshot,
                    graph_scope_id,
                    merged_revision,
                )
            except Exception as exc:
                if merged_revision is not None:
                    raise
                LOGGER.warning(
                    "Symphony observation snapshot is unavailable; planning with the current static graph.",
                    exc_info=exc,
                )
        return await self._orchestration_service.plan(
            query,
            candidate_ids,
            language=language,
            progress=progress,
            disabled_capability_ids=disabled_capability_ids,
            dynamic_overlay=snapshot.overlay if snapshot is not None else None,
            graph_revision=snapshot.static_revision if snapshot is not None else None,
            graph_snapshot=snapshot.model_dump(exclude={"overlay"}) if snapshot is not None else None,
            task_cluster_id=task_cluster_id,
            progress_callback=progress_callback,
            mode=mode,
        )

    def submit_observation(self, value: GraphEvolutionInput) -> ObservationReceipt:
        """Append one canonical execution observation for asynchronous learning."""

        return self._observation_service.submit(value)

    def get_snapshot(
        self,
        graph_scope_id: str = "default",
        merged_revision: str | None = None,
    ) -> GraphSnapshot:
        """Return one immutable static-plus-observation planner snapshot."""

        return self._observation_service.get_snapshot(graph_scope_id, merged_revision)

    def flush_observations(self, timeout: float | None = 30.0) -> None:
        """Wait for submitted observations to become visible to later plans."""

        self._observation_service.flush(timeout)

    def close(self) -> None:
        """Drain and stop the observation worker owned by this engine."""

        self._observation_service.close()

    async def _reproject_observations(self) -> None:
        if self._orchestration_service.config.dynamic_graph_enabled:
            try:
                await asyncio.to_thread(self._observation_service.reproject_all_safely)
            except Exception as exc:
                LOGGER.warning(
                    "Symphony observation reprojection failed; the published static graph remains active.",
                    exc_info=exc,
                )
