"""Unified public facade for Symphony graph capabilities."""

from __future__ import annotations

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

ProgressCallback = Callable[[OrchestrationProgress], Any]


class SymphonyGraphEngine:
    """Expose graph lifecycle operations while keeping services internal.

    ``OrchestrationService`` remains the implementation of the current static
    graph lifecycle.  The facade is the stable integration point where static
    and future observation-layer operations can share revision semantics.
    """

    def __init__(self, orchestration_service: OrchestrationService) -> None:
        self._orchestration_service = orchestration_service

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
        return await self._orchestration_service.apply_skill_graph_mutation(
            "add",
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )

    async def update_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        return await self._orchestration_service.apply_skill_graph_mutation(
            "update",
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )

    async def delete_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        return await self._orchestration_service.apply_skill_graph_mutation(
            "delete",
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )

    async def build(
        self,
        force: bool = False,
        *,
        progress: ProgressCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
    ) -> GraphBuildResult:
        return await self._orchestration_service.build(
            force,
            progress=progress,
            progress_callback=progress_callback,
            prepare_artifact=prepare_artifact,
        )

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
        dynamic_overlay: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        mode: str | None = None,
    ) -> OrchestrationPlan:
        return await self._orchestration_service.plan(
            query,
            candidate_ids,
            language=language,
            progress=progress,
            disabled_capability_ids=disabled_capability_ids,
            dynamic_overlay=dynamic_overlay,
            progress_callback=progress_callback,
            mode=mode,
        )
