"""Unified public facade for Symphony graph capabilities."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from functools import wraps
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


def _owned_operation(method):
    @wraps(method)
    async def run(self, *args, **kwargs):
        task = asyncio.current_task()
        already_owned = task in self._operations
        self._operations.add(task)
        try:
            return await method(self, *args, **kwargs)
        finally:
            if not already_owned:
                self._operations.discard(task)
    return run


class SymphonyGraphEngine:
    """Expose graph lifecycle operations while keeping services internal.

    ``OrchestrationService`` remains the implementation of the current static
    graph lifecycle.  The facade is the stable integration point where static
    and future observation-layer operations can share revision semantics.
    """

    def __init__(self, orchestration_service: OrchestrationService, *, storage=None, inventory=None) -> None:
        self._orchestration_service = orchestration_service
        self._observation_service = None
        self._storage = storage
        self._inventory = inventory
        self._closed = False
        self._build_id = ""
        self._operations: set[asyncio.Task] = set()
        self._scope_id = storage.scope_id if storage is not None else "default"

    def _check(self, *, write: bool = False, require_experience: bool = False) -> None:
        if self._closed:
            raise RuntimeError("Symphony runtime is closed")
        config = self._orchestration_service.config
        if not config.enabled:
            raise RuntimeError("Symphony orchestration is disabled")
        if require_experience and not config.experience_enabled:
            raise RuntimeError("Symphony experience is disabled")
        if self._storage is not None:
            self._storage.check(write=write)

    @property
    def _observations(self) -> GraphObservationService:
        self._check()
        if self._observation_service is None:
            self._observation_service = GraphObservationService(self._orchestration_service.graph_artifact_root)
        return self._observation_service

    def status(self, *, expected_snapshot: dict[str, Any] | None = None) -> GraphArtifactStatus:
        if not self._orchestration_service.config.enabled:
            return GraphArtifactStatus(exists=False, fresh=False, enabled=False)
        self._check()
        counts = {}
        diagnostics = ()
        if self._inventory is not None:
            scan = self._inventory.scan()
            expected_snapshot = {
                **dict(self._orchestration_service.source_snapshot or {}),
                **scan.source_snapshot.model_dump(mode="json", exclude_none=True),
            }
            current = scan.content_hashes
            try:
                published = self.read()
                previous = published.get("capabilities", ())
                if self._orchestration_service.model is None:
                    expected_snapshot["fingerprint_model_sha256"] = published.get("source_snapshot", {}).get("fingerprint_model_sha256", "")
            except FileNotFoundError:
                previous = ()
            old = {str(item.get("capability_id", item.get("id", ""))): item.get("content_hash") for item in previous}
            counts = {
                "capability_count": len(current),
                "added_count": len(current.keys() - old.keys()),
                "removed_count": len(old.keys() - current.keys()),
                "changed_count": sum(current[key] != old[key] for key in current.keys() & old.keys()),
                "reused_count": sum(current[key] == old[key] for key in current.keys() & old.keys()),
            }
            diagnostics = tuple(item.to_dict() for item in scan.diagnostics)
        status = self._orchestration_service.status(expected_snapshot=expected_snapshot)
        return replace(status, counts=counts, diagnostics=diagnostics)

    def read(self, version: str | None = None) -> CapabilityGraph:
        self._check()
        return self._orchestration_service.read(version)

    @_owned_operation
    async def add_capabilities(
        self,
        changed_capability_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        self._check(write=True)
        result = await self._orchestration_service.apply_skill_graph_mutation(
            "add",
            changed_capability_ids,
            request_id=request_id,
            source_revision=source_revision,
        )
        await self._reproject_observations()
        return result

    @_owned_operation
    async def update_capabilities(
        self,
        changed_capability_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        self._check(write=True)
        result = await self._orchestration_service.apply_skill_graph_mutation(
            "update",
            changed_capability_ids,
            request_id=request_id,
            source_revision=source_revision,
        )
        await self._reproject_observations()
        return result

    @_owned_operation
    async def delete_capabilities(
        self,
        changed_capability_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        self._check(write=True)
        result = await self._orchestration_service.apply_skill_graph_mutation(
            "delete",
            changed_capability_ids,
            request_id=request_id,
            source_revision=source_revision,
        )
        await self._reproject_observations()
        return result

    @_owned_operation
    async def build(
        self,
        force: bool = False,
        *,
        progress: ProgressCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
    ) -> GraphBuildResult:
        self._check(write=True)
        if self._orchestration_service._build_task is not None and not self._orchestration_service._build_task.done():
            raise RuntimeError("A Symphony graph build is already running")
        before = self.status()
        result = await self._orchestration_service.build(
            force,
            progress=progress,
            progress_callback=progress_callback,
            prepare_artifact=prepare_artifact,
        )
        await self._reproject_observations()
        payload = self.read()
        counts = dict(getattr(self._orchestration_service.fingerprint_service, "last_build_counts", {}))
        counts["edge_count"] = len(payload.get("edges", ()))
        llm = payload.get("config", {}).get("llm", {})
        relation = llm.get("relation_cache", {})
        counts["relation_reused_count"] = relation.get("reused_count", 0)
        counts["relation_resolved_count"] = relation.get("resolved_count", 0)
        counts["graph_reused_count"] = 0
        counts.update(result.counts)
        self._build_id = result.build_id
        counts.update({key: value for key, value in before.counts.items() if key in {"added_count", "changed_count", "removed_count"}})
        return replace(result, build_id=self._build_id, counts=counts,
                       diagnostics=tuple(payload.get("diagnostics", ())))

    async def cancel_build(self) -> GraphArtifactStatus:
        self._check()
        task = self._orchestration_service._build_task
        await self._orchestration_service.cancel_build()
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.status()

    @_owned_operation
    async def plan(
        self,
        query: str,
        candidate_ids: Sequence[str] | None = None,
        *,
        language: str = "cn",
        progress: ProgressCallback | None = None,
        disabled_capability_ids: Sequence[str] | None = None,
        graph_scope_id: str | None = None,
        merged_revision: str | None = None,
        task_cluster_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
        mode: str | None = None,
    ) -> OrchestrationPlan:
        self._check()
        snapshot = None
        if self._orchestration_service.config.plan.dynamic_graph_enabled:
            try:
                snapshot = await asyncio.to_thread(
                    self._observations.get_snapshot,
                    graph_scope_id or self._scope_id,
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

        self._check(write=True, require_experience=True)
        return self._observations.submit(value)

    def get_snapshot(
        self,
        graph_scope_id: str | None = None,
        merged_revision: str | None = None,
    ) -> GraphSnapshot:
        """Return one immutable static-plus-observation planner snapshot."""

        return self._observations.get_snapshot(graph_scope_id or self._scope_id, merged_revision)

    def flush_observations(self, timeout: float | None = 30.0) -> None:
        """Wait for submitted observations to become visible to later plans."""

        if self._observation_service is not None:
            self._observation_service.flush(timeout)

    def close(self) -> None:
        """Drain and stop the observation worker owned by this engine."""

        self._closed = True
        if self._observation_service is not None:
            self._observation_service.close()

    async def aclose(self) -> None:
        self._closed = True
        tasks = {task for task in self._operations if task is not asyncio.current_task() and not task.done()}
        build_task = self._orchestration_service._build_task
        if build_task is not None and build_task is not asyncio.current_task() and not build_task.done():
            tasks.add(build_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(self.close)

    async def _reproject_observations(self) -> None:
        if self._orchestration_service.config.plan.dynamic_graph_enabled:
            try:
                await asyncio.to_thread(self._observations.reproject_all_safely)
            except Exception as exc:
                LOGGER.warning(
                    "Symphony observation reprojection failed; the published static graph remains active.",
                    exc_info=exc,
                )
