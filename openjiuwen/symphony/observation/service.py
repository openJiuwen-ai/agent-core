"""Qualification, aggregation, rebinding, and snapshot services."""

from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from openjiuwen.symphony.observation.contracts import (
    EvidenceStrength,
    FailureDomain,
    GraphEvolutionInput,
    GraphSnapshot,
    ObservationReceipt,
    TaskOutcomeLabel,
)
from openjiuwen.symphony.observation.identity import (
    EdgeIdentity,
    StaticGraphIndex,
    build_static_graph_index,
    edge_identity_from_observation,
    stable_hash,
)
from openjiuwen.symphony.observation.store import EvidenceStore, RevisionStore
from openjiuwen.symphony.orchestration.artifacts import GraphArtifactStore

MIN_RUNTIME_ONLY_SUCCESS_SESSIONS = 2
RUNTIME_ADJUSTMENT_STEP = 0.05
MIN_RUNTIME_ADJUSTMENT = -1.0
MAX_RUNTIME_ADJUSTMENT = 1.0
LOGGER = logging.getLogger(__name__)


class GraphObservationService:
    """Own the append-only evidence log and derived graph revisions."""

    def __init__(self, graph_artifact_root: str | Path) -> None:
        self._static_store = GraphArtifactStore(graph_artifact_root)
        self._evidence_store = EvidenceStore(graph_artifact_root)
        self._revision_store = RevisionStore(graph_artifact_root)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="symphony-observation")
        self._aggregate_lock = threading.Lock()
        self._future_lock = threading.Lock()
        self._futures: set[Future[GraphSnapshot]] = set()
        self._scheduled_scopes: set[str] = set()
        self._dirty_scopes: set[str] = set()
        self._worker_errors: list[BaseException] = []
        self._static_index_lock = threading.Lock()
        self._static_index_cache: dict[str, StaticGraphIndex] = {}

    def submit(self, value: GraphEvolutionInput) -> ObservationReceipt:
        """Append evidence in the request thread and aggregate it asynchronously."""

        try:
            static_index = self._static_index(value.graph_snapshot.static_revision)
        except FileNotFoundError:
            current_revision, current_index = self._current_static_index()
            eligible, reason, normalized = self._qualify(value, current_revision, current_index)
            eligible = False
            reason = ",".join(filter(None, ("unknown_static_revision", reason)))
        else:
            eligible, reason, normalized = self._qualify(
                value,
                value.graph_snapshot.static_revision,
                static_index,
            )
        append_result = self._evidence_store.append(
            graph_scope_id=value.graph_scope_id,
            evidence_id=value.evidence_id,
            observed_at=value.observed_at.isoformat(),
            eligible=eligible,
            reason=reason,
            payload=normalized,
        )
        if not append_result.duplicate:
            self._schedule(value.graph_scope_id)
        status: Literal["accepted", "audit_only", "duplicate"] = (
            "duplicate" if append_result.duplicate else ("accepted" if eligible else "audit_only")
        )
        return ObservationReceipt(
            evidence_id=value.evidence_id,
            graph_scope_id=value.graph_scope_id,
            sequence=append_result.sequence,
            status=status,
            reason=append_result.reason,
        )

    def get_snapshot(
        self,
        graph_scope_id: str = "default",
        merged_revision: str | None = None,
    ) -> GraphSnapshot:
        """Return a snapshot bound to the current static revision."""

        static_revision = self._current_static_index()[0]
        snapshot = self._revision_store.read_snapshot(graph_scope_id, merged_revision)
        if merged_revision is not None:
            if snapshot is None:
                raise FileNotFoundError(f"Unknown Symphony merged revision: {merged_revision}")
            return snapshot
        needs_projection = (
            snapshot is None
            or snapshot.static_revision != static_revision
            or snapshot.overlay.get("projection_status") == "safe_fallback"
        )
        if needs_projection:
            try:
                snapshot = self.reproject(graph_scope_id)
            except Exception as exc:
                LOGGER.warning(
                    "Symphony observation projection failed; using the current static graph with neutral weights.",
                    exc_info=exc,
                )
                snapshot = self.publish_safe_fallback(graph_scope_id, reason=type(exc).__name__)
        return snapshot

    def flush(self, timeout: float | None = 30.0) -> None:
        """Wait until all observations submitted so far have been aggregated."""

        with self._future_lock:
            futures = tuple(self._futures)
        if futures:
            completed, pending = wait(futures, timeout=timeout)
            if pending:
                raise TimeoutError("Timed out waiting for Symphony graph observations to aggregate.")
            for future in completed:
                future.result()
        with self._future_lock:
            if self._worker_errors:
                error = self._worker_errors.pop(0)
                raise RuntimeError("Symphony observation worker failed.") from error

    def reproject(self, graph_scope_id: str = "default") -> GraphSnapshot:
        """Rebind accumulated observations after a static graph switch."""

        with self._aggregate_lock:
            return self._aggregate(graph_scope_id)

    def reproject_all(self) -> tuple[GraphSnapshot, ...]:
        """Rebind every known scope after a static graph publication."""

        self.flush()
        scopes = self._evidence_store.scope_ids() or ("default",)
        return tuple(self.reproject(scope) for scope in scopes)

    def reproject_all_safely(self) -> tuple[GraphSnapshot, ...]:
        """Rebind all scopes, falling back to neutral observations per scope."""

        try:
            self.flush()
        except Exception as exc:
            LOGGER.warning("Symphony observation worker failed before static rebinding.", exc_info=exc)
        snapshots = []
        for graph_scope_id in self._evidence_store.scope_ids() or ("default",):
            try:
                snapshots.append(self.reproject(graph_scope_id))
            except Exception as exc:
                LOGGER.warning(
                    "Symphony observation rebinding failed for scope %s; publishing a neutral fallback.",
                    graph_scope_id,
                    exc_info=exc,
                )
                snapshots.append(self.publish_safe_fallback(graph_scope_id, reason=type(exc).__name__))
        return tuple(snapshots)

    def publish_safe_fallback(self, graph_scope_id: str, *, reason: str) -> GraphSnapshot:
        """Bind the current static revision to an empty, explicitly retryable overlay."""

        static_revision = self._current_static_index()[0]
        return self._revision_store.publish(
            graph_scope_id=graph_scope_id,
            static_revision=static_revision,
            high_water_sequence=0,
            edges={},
            generated_at=datetime.now(timezone.utc).isoformat(),
            projection_status="safe_fallback",
            diagnostics=(reason,),
        )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _schedule(self, graph_scope_id: str) -> None:
        with self._future_lock:
            self._dirty_scopes.add(graph_scope_id)
            if graph_scope_id in self._scheduled_scopes:
                return
            self._scheduled_scopes.add(graph_scope_id)
            future = self._executor.submit(self._drain_scope, graph_scope_id)
            self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def _drain_scope(self, graph_scope_id: str) -> GraphSnapshot:
        try:
            while True:
                with self._future_lock:
                    self._dirty_scopes.discard(graph_scope_id)
                snapshot = self.reproject(graph_scope_id)
                with self._future_lock:
                    if graph_scope_id not in self._dirty_scopes:
                        self._scheduled_scopes.discard(graph_scope_id)
                        return snapshot
        except Exception:
            with self._future_lock:
                self._scheduled_scopes.discard(graph_scope_id)
            raise

    def _discard_future(self, future: Future[GraphSnapshot]) -> None:
        with self._future_lock:
            self._futures.discard(future)
            if not future.cancelled() and future.exception() is not None:
                self._worker_errors.append(future.exception())

    def _aggregate(self, graph_scope_id: str) -> GraphSnapshot:
        with self._revision_store.projection_lock(graph_scope_id):
            static_revision, static_index = self._current_static_index()
            current = self._revision_store.read_overlay(graph_scope_id) or {}
            high_water_sequence = int(current.get("high_water_sequence") or 0)
            edges = {
                str(key): dict(value)
                for key, value in (current.get("edges") or {}).items()
                if isinstance(value, Mapping)
            }
            for sequence, eligible, payload in self._evidence_store.iter_after(
                graph_scope_id,
                high_water_sequence,
            ):
                high_water_sequence = sequence
                if eligible:
                    self._apply_evidence(edges, payload, static_index)
            for stats in edges.values():
                self._rebind(stats, static_index)
            return self._revision_store.publish(
                graph_scope_id=graph_scope_id,
                static_revision=static_revision,
                high_water_sequence=high_water_sequence,
                edges=edges,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )

    def _qualify(
        self,
        value: GraphEvolutionInput,
        static_revision: str,
        static_index: StaticGraphIndex,
    ) -> tuple[bool, str, dict[str, Any]]:
        reasons = []
        if value.trace.truncated:
            reasons.append("truncated_trace")
        if value.trace.quality_flags:
            reasons.append("trace_quality_flags")
        if value.task.outcome.evidence_strength != EvidenceStrength.STRONG:
            reasons.append("outcome_not_strong")
        elif not value.task.outcome.evidence_refs:
            reasons.append("strong_outcome_missing_evidence_ref")
        if not static_index.validates_capabilities(value.capabilities):
            reasons.append("capability_identity_mismatch")
        if value.task.outcome.label == TaskOutcomeLabel.VERIFIED_SUCCESS and any(
            edge.metadata.success is False for edge in value.execution_graph.edges
        ):
            reasons.append("task_edge_outcome_conflict")
        snapshot_reason = self._snapshot_reference_reason(value)
        if snapshot_reason:
            reasons.append(snapshot_reason)

        capabilities = value.capabilities
        normalized_edges = []
        for edge in value.execution_graph.edges:
            identity = edge_identity_from_observation(edge, capabilities, static_index)
            if identity is None:
                continue
            outcome = self._edge_outcome(
                value,
                edge.metadata.success,
                edge.metadata.failure_domain,
                bool(edge.metadata.evidence_refs),
            )
            if outcome is None:
                continue
            normalized_edges.append(
                {
                    **identity.to_dict(),
                    "edge_identity": identity.identity_hash,
                    "outcome": outcome,
                    "evidence_refs": sorted(set(edge.metadata.evidence_refs)),
                    "observed_as_static": identity.identity_hash in static_index.edges_by_identity,
                }
            )
        if not normalized_edges:
            reasons.append("no_qualified_edge_outcome")

        normalized = {
            "schema_version": value.schema_version,
            "evidence_id": value.evidence_id,
            "graph_scope_id": value.graph_scope_id,
            "observed_at": value.observed_at.isoformat(),
            "static_revision": value.graph_snapshot.static_revision,
            "trajectory_id_hash": _private_hash(value.trace.trajectory_id),
            "session_id_hash": _private_hash(value.trace.session_id),
            "request_id_hash": _private_hash(value.trace.request_id) if value.trace.request_id else None,
            "capture_mode": value.trace.capture_mode,
            "trace_refs": sorted(set(value.trace.span_refs) | set(value.trace.member_trajectory_refs)),
            "task_outcome": {
                "label": value.task.outcome.label.value,
                "evidence_strength": value.task.outcome.evidence_strength.value,
                "failure_domain": (
                    value.task.outcome.failure_domain.value if value.task.outcome.failure_domain is not None else None
                ),
                "evidence_refs": sorted(set(value.task.outcome.evidence_refs)),
            },
            "task_cluster_id": value.task.task_cluster_id,
            "edges": normalized_edges,
        }
        return not reasons, ",".join(reasons), normalized

    def _snapshot_reference_reason(self, value: GraphEvolutionInput) -> str:
        merged_revision = value.graph_snapshot.merged_revision
        if merged_revision is None:
            return ""
        try:
            snapshot = self._revision_store.read_snapshot(
                value.graph_scope_id,
                merged_revision,
            )
        except FileNotFoundError:
            return "unknown_merged_revision"
        if snapshot is None:
            return "unknown_merged_revision"
        if snapshot.static_revision != value.graph_snapshot.static_revision:
            return "merged_static_revision_mismatch"
        if snapshot.observation_revision != value.graph_snapshot.observation_revision:
            return "merged_observation_revision_mismatch"
        return ""

    @staticmethod
    def _edge_outcome(
        value: GraphEvolutionInput,
        edge_success: bool | None,
        edge_failure_domain: FailureDomain | None,
        has_edge_evidence: bool,
    ) -> str | None:
        if value.task.outcome.label == TaskOutcomeLabel.VERIFIED_SUCCESS:
            return "success" if edge_success is True and has_edge_evidence else None
        if value.task.outcome.label != TaskOutcomeLabel.VERIFIED_FAILURE:
            return None
        is_explicit_failure = edge_success is False and has_edge_evidence
        if not is_explicit_failure:
            return None
        is_orchestration_failure = (
            edge_failure_domain == FailureDomain.ORCHESTRATION
            and value.task.outcome.failure_domain == FailureDomain.ORCHESTRATION
        )
        return "failure" if is_orchestration_failure else None

    @staticmethod
    def _apply_evidence(
        edges: dict[str, dict[str, Any]],
        payload: Mapping[str, Any],
        static_index: StaticGraphIndex,
    ) -> None:
        session_hash = str(payload.get("session_id_hash") or "")
        static_revision = str(payload.get("static_revision") or "")
        task_cluster_id = str(payload.get("task_cluster_id") or "").strip()
        observed_at = str(payload.get("observed_at") or "")
        for item in payload.get("edges") or []:
            if not isinstance(item, Mapping):
                continue
            edge_identity = str(item.get("edge_identity") or "")
            if not edge_identity:
                continue
            stats = edges.setdefault(
                edge_identity,
                {
                    "edge_identity": edge_identity,
                    "source_id": item.get("source_id"),
                    "target_id": item.get("target_id"),
                    "relation_type": item.get("relation_type"),
                    "source_content_hash": item.get("source_content_hash"),
                    "target_content_hash": item.get("target_content_hash"),
                    "port_mappings": list(item.get("port_mappings") or []),
                    "port_mapping_hash": stable_hash(item.get("port_mappings") or []),
                    "success_count": 0,
                    "failure_count": 0,
                    "attempt_count": 0,
                    "success_session_hashes": [],
                    "success_sessions_by_revision": {},
                    "failure_session_hashes": [],
                    "task_cluster_ids": [],
                    "ever_static": False,
                    "first_observed_at": observed_at,
                },
            )
            outcome = str(item.get("outcome") or "")
            success_sessions = list(stats.get("success_session_hashes") or [])
            failure_sessions = list(stats.get("failure_session_hashes") or [])
            if outcome == "success":
                if session_hash and session_hash not in success_sessions:
                    stats["success_count"] = int(stats.get("success_count") or 0) + 1
                _append_unique(stats, "success_session_hashes", session_hash)
                sessions_by_revision = dict(stats.get("success_sessions_by_revision") or {})
                revision_sessions = list(sessions_by_revision.get(static_revision) or [])
                if session_hash and session_hash not in revision_sessions:
                    revision_sessions.append(session_hash)
                sessions_by_revision[static_revision] = sorted(revision_sessions)
                stats["success_sessions_by_revision"] = sessions_by_revision
            elif outcome == "failure":
                if session_hash and session_hash not in failure_sessions:
                    stats["failure_count"] = int(stats.get("failure_count") or 0) + 1
                _append_unique(stats, "failure_session_hashes", session_hash)
            stats["attempt_count"] = len(
                set(stats.get("success_session_hashes") or []) | set(stats.get("failure_session_hashes") or [])
            )
            if task_cluster_id:
                _append_unique(stats, "task_cluster_ids", task_cluster_id)
            stats["last_observed_at"] = observed_at
            stats["last_evidence_static_revision"] = static_revision
            stats["ever_static"] = (
                bool(stats.get("ever_static"))
                or bool(item.get("observed_as_static"))
                or edge_identity in static_index.edges_by_identity
            )
            stats["runtime_adjustment"] = _runtime_adjustment(stats)
            stats["runtime_weight"] = 1.0 + stats["runtime_adjustment"]

    @staticmethod
    def _rebind(stats: dict[str, Any], static_index: StaticGraphIndex) -> None:
        source_id = str(stats.get("source_id") or "")
        target_id = str(stats.get("target_id") or "")
        edge_identity = str(stats.get("edge_identity") or "")
        if source_id not in static_index.capability_ids or target_id not in static_index.capability_ids:
            stats["binding_status"] = "orphaned"
        elif static_index.content_hash_by_id.get(source_id) != stats.get(
            "source_content_hash"
        ) or static_index.content_hash_by_id.get(target_id) != stats.get("target_content_hash"):
            stats["binding_status"] = "invalidated"
        elif edge_identity in static_index.edges_by_identity:
            stats["binding_status"] = "active_static"
            stats["ever_static"] = True
        elif bool(stats.get("ever_static")) and stats.get("last_evidence_static_revision") != static_index.revision:
            stats["binding_status"] = "quarantined"
        elif not static_index.validates_mapping(_identity_from_stats(stats)):
            stats["binding_status"] = "invalidated"
        elif (
            len((stats.get("success_sessions_by_revision") or {}).get(static_index.revision) or [])
            >= MIN_RUNTIME_ONLY_SUCCESS_SESSIONS
            and int(stats.get("failure_count") or 0) == 0
        ):
            stats["binding_status"] = "active_runtime_only"
        else:
            stats["binding_status"] = "insufficient_evidence"
        stats["runtime_adjustment"] = _runtime_adjustment(stats)
        stats["runtime_weight"] = 1.0 + stats["runtime_adjustment"]

    def _current_static_index(self) -> tuple[str, StaticGraphIndex]:
        revision = self._static_store.current_version()
        if not revision:
            raise FileNotFoundError("Symphony static graph must be published before observations are accepted.")
        return revision, self._static_index(revision)

    def _static_index(self, revision: str) -> StaticGraphIndex:
        with self._static_index_lock:
            cached = self._static_index_cache.get(revision)
            if cached is not None:
                return cached
            payload = self._static_store.read(revision)
            index = build_static_graph_index(revision, payload)
            self._static_index_cache[revision] = index
            return index


def _identity_from_stats(stats: Mapping[str, Any]) -> EdgeIdentity:
    return EdgeIdentity(
        source_id=str(stats.get("source_id") or ""),
        target_id=str(stats.get("target_id") or ""),
        relation_type=str(stats.get("relation_type") or "can_feed"),
        source_content_hash=str(stats.get("source_content_hash") or ""),
        target_content_hash=str(stats.get("target_content_hash") or ""),
        port_mappings=tuple(dict(item) for item in stats.get("port_mappings") or [] if isinstance(item, Mapping)),
    )


def _append_unique(stats: dict[str, Any], key: str, value: str) -> None:
    if not value:
        return
    values = list(stats.get(key) or [])
    if value not in values:
        values.append(value)
    stats[key] = sorted(values)


def _runtime_adjustment(stats: Mapping[str, Any]) -> float:
    success_count = int(stats.get("success_count") or 0)
    failure_count = int(stats.get("failure_count") or 0)
    raw = RUNTIME_ADJUSTMENT_STEP * (success_count - failure_count)
    return max(MIN_RUNTIME_ADJUSTMENT, min(MAX_RUNTIME_ADJUSTMENT, raw))


def _private_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
