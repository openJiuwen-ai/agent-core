# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Atomic batch Skill graph mutation workflow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.models import CapabilityFingerprint, SourceSnapshot
from openjiuwen.symphony.orchestration.artifacts import GraphArtifactStore
from openjiuwen.symphony.orchestration.contracts import GraphMutationDelta, GraphMutationResult
from openjiuwen.symphony.orchestration.graph.matcher.ontology import OntologyMatcher
from openjiuwen.symphony.orchestration.mutations import (
    MutationJournal,
    MutationOperation,
    MutationRequest,
    artifact_identity_hashes,
    artifact_provider_snapshot,
    build_protocol_hash,
    canonical_fingerprints_by_identity,
    graph_delta,
    inventory_changes,
    mutation_result_dict,
    mutation_version,
    prepare_mutation_request,
    raise_mutation_error,
    run_prepare_artifact,
    snapshot_stability_payload,
    stable_sha256,
    validate_content_hash_identity,
    validate_mutation_against_inventory,
)
from openjiuwen.symphony.shared.fingerprint import Fingerprint

InventoryLoader = Callable[
    [],
    Awaitable[tuple[list[Fingerprint], SourceSnapshot | None, list[CapabilityFingerprint] | None]],
]
MatcherFactory = Callable[..., OntologyMatcher]
SnapshotFactory = Callable[..., dict[str, Any]]
GraphPayloadBuilder = Callable[..., Awaitable[tuple[dict[str, Any], tuple[dict[str, Any], ...]]]]
PrepareArtifactHook = Callable[[Path], Any]


@dataclass(frozen=True)
class _ActiveMutationState:
    current_version: str
    current_payload: dict[str, Any]
    current_provider_snapshot: SourceSnapshot
    content_hashes: dict[str, str]
    graph_identity_hashes: dict[str, str]


@dataclass(frozen=True)
class _MutationState:
    active: _ActiveMutationState
    target_capabilities: tuple[Fingerprint, ...]
    target_snapshot: SourceSnapshot
    changes: dict[str, set[str]]
    matcher: OntologyMatcher
    target_build_snapshot: dict[str, Any]


class GraphMutationCoordinator:
    """Validate, build, and atomically publish one batch graph mutation."""

    def __init__(
        self,
        *,
        store: GraphArtifactStore,
        journal: MutationJournal,
        write_lock: asyncio.Lock,
        model_available: bool,
        inventory_loader: InventoryLoader,
        matcher_factory: MatcherFactory,
        snapshot_factory: SnapshotFactory,
        graph_builder: GraphPayloadBuilder,
        prepare_artifact: PrepareArtifactHook | None,
    ) -> None:
        self._store = store
        self._journal = journal
        self._write_lock = write_lock
        self._model_available = model_available
        self._inventory_loader = inventory_loader
        self._matcher_factory = matcher_factory
        self._snapshot_factory = snapshot_factory
        self._graph_builder = graph_builder
        self._prepare_artifact = prepare_artifact

    async def mutate(
        self,
        operation: MutationOperation,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        """Apply one validated all-or-nothing mutation batch."""

        request = prepare_mutation_request(
            operation,
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )
        async with self._write_lock:
            replay = self._journal.read(request.request_id, request.request_digest)
            if replay is not None:
                return replay
            state = await self._prepare_state(request)
            noop = self._noop_result(request, state)
            if noop is not None:
                self._journal.record(request.request_digest, noop)
                return noop
            return await self._build_and_publish(request, state)

    def _read_active_state(self, request: MutationRequest) -> _ActiveMutationState:
        current_version = self._store.current_version()
        if current_version is None:
            raise_mutation_error(
                "full_rebuild_required",
                "batch graph mutation requires an existing graph; call build() first",
                affected_ids=request.affected_ids,
            )
        if not self._model_available:
            raise_mutation_error(
                "full_rebuild_required",
                "batch graph mutation requires a model for ontology matching",
                current_version=current_version,
                affected_ids=request.affected_ids,
            )
        payload = self._store.read(current_version)
        content_hashes, graph_hashes = artifact_identity_hashes(
            payload,
            current_version=current_version,
            affected_ids=request.affected_ids,
        )
        provider_snapshot = artifact_provider_snapshot(
            payload,
            current_version=current_version,
            affected_ids=request.affected_ids,
        )
        return _ActiveMutationState(
            current_version=current_version,
            current_payload=payload,
            current_provider_snapshot=provider_snapshot,
            content_hashes=content_hashes,
            graph_identity_hashes=graph_hashes,
        )

    async def _prepare_state(self, request: MutationRequest) -> _MutationState:
        active = self._read_active_state(request)
        capabilities, snapshot, fingerprints = await self._inventory_loader()
        if snapshot is None or fingerprints is None:
            raise_mutation_error(
                "full_rebuild_required",
                "batch graph mutation requires AtomicCapabilityProvider.inventory_snapshot()",
                current_version=active.current_version,
                affected_ids=request.affected_ids,
            )
        self._validate_target_snapshot(request, snapshot, active.current_version)
        target_by_identity = canonical_fingerprints_by_identity(
            fingerprints,
            current_version=active.current_version,
            affected_ids=request.affected_ids,
        )
        target_hashes = {key: item.content_hash for key, item in target_by_identity.items()}
        target_graph_hashes = {
            key: stable_sha256(item.graph_identity_dict()) for key, item in target_by_identity.items()
        }
        validate_content_hash_identity(
            active.content_hashes,
            active.graph_identity_hashes,
            target_hashes,
            target_graph_hashes,
            current_version=active.current_version,
            affected_ids=request.affected_ids,
        )
        changes = inventory_changes(active.content_hashes, target_hashes)
        validate_mutation_against_inventory(
            request.operation,
            request.affected_ids,
            current_hashes=active.content_hashes,
            target_by_identity=target_by_identity,
            changes=changes,
            current_version=active.current_version,
            affected_ids=request.affected_ids,
        )
        matcher = self._matcher_factory(capabilities, force=False)
        build_snapshot = self._snapshot_factory(
            capabilities,
            matcher.identity_metadata(),
            provider_snapshot=snapshot,
        )
        if active.current_payload.get("build_protocol_hash") != build_protocol_hash(build_snapshot):
            raise_mutation_error(
                "full_rebuild_required",
                "graph construction protocol changed; call build(force=True) before applying mutations",
                current_version=active.current_version,
                affected_ids=request.affected_ids,
            )
        return _MutationState(
            active=active,
            target_capabilities=tuple(capabilities),
            target_snapshot=snapshot,
            changes=changes,
            matcher=matcher,
            target_build_snapshot=build_snapshot,
        )

    @staticmethod
    def _validate_target_snapshot(
        request: MutationRequest,
        target_snapshot: SourceSnapshot,
        current_version: str,
    ) -> None:
        if not target_snapshot.content_hash.strip():
            raise_mutation_error(
                "source_snapshot_mismatch",
                "the target source snapshot must include content_hash",
                current_version=current_version,
                affected_ids=request.affected_ids,
            )
        if request.source_revision != target_snapshot.snapshot_id:
            raise_mutation_error(
                "source_snapshot_mismatch",
                "source_revision does not match the provider target snapshot",
                current_version=current_version,
                affected_ids=request.affected_ids,
                details={
                    "requested_source_revision": request.source_revision,
                    "provider_snapshot_id": target_snapshot.snapshot_id,
                },
            )

    @staticmethod
    def _noop_result(
        request: MutationRequest,
        state: _MutationState,
    ) -> GraphMutationResult | None:
        if request.operation != "update" or any(state.changes.values()):
            return None
        if snapshot_stability_payload(state.active.current_provider_snapshot) != snapshot_stability_payload(
            state.target_snapshot
        ):
            raise_mutation_error(
                "change_set_mismatch",
                "the source snapshot changed without a corresponding Skill content change",
                current_version=state.active.current_version,
                affected_ids=request.affected_ids,
            )
        return GraphMutationResult(
            request_id=request.request_id,
            operation=request.operation,
            status="noop",
            previous_version=state.active.current_version,
            version=state.active.current_version,
            source_snapshot_id=state.target_snapshot.snapshot_id,
            changed_capability_ids=request.affected_ids,
            delta=GraphMutationDelta(),
        )

    async def _build_and_publish(
        self,
        request: MutationRequest,
        state: _MutationState,
    ) -> GraphMutationResult:
        try:
            payload, diagnostics = await self._graph_builder(
                state.target_capabilities,
                matcher=state.matcher,
                snapshot=state.target_build_snapshot,
                provider_snapshot=state.target_snapshot,
            )
        except BaseError:
            raise
        except Exception as exc:  # noqa: BLE001 -- matcher backends expose provider-specific errors.
            raise_mutation_error(
                "matcher_failed",
                f"graph relation matching failed ({type(exc).__name__})",
                current_version=state.active.current_version,
                affected_ids=request.affected_ids,
                cause=exc,
                status=StatusCode.COMPONENT_SYMPHONY_BUILD_RUNTIME_ERROR,
            )
        result = self._result(request, state, payload, diagnostics)
        payload["mutation"] = {
            "request_id": request.request_id,
            "request_digest": request.request_digest,
            "operation": request.operation,
            "source_revision": request.source_revision,
            "previous_version": state.active.current_version,
            "changed_capability_ids": list(request.affected_ids),
            "result": mutation_result_dict(result),
        }
        await self._publish_payload(request, state, payload, result)
        self._journal.record(request.request_digest, result)
        return result

    @staticmethod
    def _result(
        request: MutationRequest,
        state: _MutationState,
        payload: dict[str, Any],
        diagnostics: Sequence[dict[str, Any]],
    ) -> GraphMutationResult:
        return GraphMutationResult(
            request_id=request.request_id,
            operation=request.operation,
            status="published",
            previous_version=state.active.current_version,
            version=mutation_version(state.target_snapshot, request.request_digest),
            source_snapshot_id=state.target_snapshot.snapshot_id,
            changed_capability_ids=request.affected_ids,
            delta=graph_delta(
                state.active.current_payload,
                payload,
            ),
            diagnostics=tuple(diagnostics),
        )

    async def _publish_payload(
        self,
        request: MutationRequest,
        state: _MutationState,
        payload: dict[str, Any],
        result: GraphMutationResult,
    ) -> None:
        try:
            staged = await asyncio.to_thread(
                self._store.stage,
                payload,
                version=result.version,
                reuse_existing=True,
            )
            await run_prepare_artifact(self._prepare_artifact, staged.graph_path.parent)
            published = self._store.activate_if_current(
                staged,
                expected_version=state.active.current_version,
            )
        except BaseError:
            raise
        except Exception as exc:  # noqa: BLE001 -- filesystems expose application-specific errors.
            raise_mutation_error(
                "publish_failed",
                f"graph mutation publish failed ({type(exc).__name__})",
                current_version=self._store.current_version(),
                affected_ids=request.affected_ids,
                cause=exc,
                status=StatusCode.COMPONENT_SYMPHONY_ARTIFACT_WRITE_CALL_FAILED,
            )
        if published.version != result.version:
            raise_mutation_error(
                "artifact_invalid",
                "published graph version does not match the mutation result",
                current_version=self._store.current_version(),
                affected_ids=request.affected_ids,
            )
