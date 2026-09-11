# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure contracts and validation helpers for atomic Skill graph mutations."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn, Sequence

from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, raise_error
from openjiuwen.symphony.models import CapabilityFingerprint, SourceSnapshot
from openjiuwen.symphony.orchestration.artifacts import GraphArtifactStore
from openjiuwen.symphony.orchestration.contracts import (
    GraphMutationDelta,
    GraphMutationResult,
)
from openjiuwen.symphony.shared.fingerprint import Fingerprint, coerce_fingerprint

MutationOperation = Literal["add", "update", "delete"]


@dataclass(frozen=True)
class MutationRequest:
    """Normalized, validated batch request used by the publication workflow."""

    operation: MutationOperation
    request_id: str
    source_revision: str
    affected_ids: tuple[str, ...]
    request_digest: str


def prepare_mutation_request(
    operation: MutationOperation,
    changed_skill_ids: Sequence[str],
    *,
    request_id: str,
    source_revision: str,
) -> MutationRequest:
    """Normalize and validate one public mutation call."""

    normalized_request_id = str(request_id or "").strip()
    normalized_source_revision = str(source_revision or "").strip()
    affected_ids = validate_mutation_batch(
        changed_skill_ids,
        request_id=normalized_request_id,
        source_revision=normalized_source_revision,
    )
    request_digest = mutation_request_digest(
        operation,
        affected_ids,
        request_id=normalized_request_id,
        source_revision=normalized_source_revision,
    )
    return MutationRequest(
        operation=operation,
        request_id=normalized_request_id,
        source_revision=normalized_source_revision,
        affected_ids=affected_ids,
        request_digest=request_digest,
    )


class MutationJournal:
    """Durable idempotency lookup backed by an index and current artifact recovery."""

    def __init__(self, store: GraphArtifactStore) -> None:
        self._store = store

    def read(self, request_id: str, request_digest: str) -> GraphMutationResult | None:
        """Return a prior result or reject reuse of an idempotency key."""

        record = self._store.read_mutation_request(request_id)
        if record is not None:
            self._validate_digest(record.get("request_digest"), request_digest)
            return mutation_result_from_dict(record.get("result"))
        try:
            current_payload = self._store.read()
        except FileNotFoundError:
            return None
        mutation = current_payload.get("mutation")
        if not isinstance(mutation, dict) or mutation.get("request_id") != request_id:
            return None
        self._validate_digest(mutation.get("request_digest"), request_digest)
        result = mutation_result_from_dict(mutation.get("result"))
        self._store.record_mutation_request(
            request_id,
            request_digest=request_digest,
            result=mutation_result_dict(result),
        )
        return result

    def record(self, request_digest: str, result: GraphMutationResult) -> None:
        """Persist one successful published or no-op result."""

        try:
            self._store.record_mutation_request(
                result.request_id,
                request_digest=request_digest,
                result=mutation_result_dict(result),
            )
        except BaseError:
            raise
        except Exception as exc:  # noqa: BLE001 -- filesystems expose platform-specific errors.
            raise_mutation_error(
                "publish_failed",
                f"failed to persist graph mutation idempotency state ({type(exc).__name__})",
                current_version=self._store.current_version(),
                affected_ids=result.changed_capability_ids,
                cause=exc,
                status=StatusCode.COMPONENT_SYMPHONY_ARTIFACT_WRITE_CALL_FAILED,
            )

    def _validate_digest(self, stored_digest: Any, requested_digest: str) -> None:
        if stored_digest != requested_digest:
            raise_mutation_error(
                "request_id_conflict",
                "request_id already refers to a different graph mutation",
                current_version=self._store.current_version(),
            )


async def load_inventory(
    provider: Any,
    *,
    require_atomic: bool = False,
    current_version: str | None = None,
) -> tuple[list[Fingerprint], SourceSnapshot | None, list[CapabilityFingerprint] | None]:
    """Load a stable provider inventory, preferring the atomic extension."""

    atomic_loader = getattr(provider, "inventory_snapshot", None)
    provider_snapshot: SourceSnapshot | None = None
    raw_capabilities: Any
    if callable(atomic_loader):
        try:
            inventory_snapshot = await atomic_loader()
        except Exception as exc:  # noqa: BLE001 -- providers expose application-specific failures.
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_BUILD_RUNTIME_ERROR,
                cause=exc,
                reason=f"capability provider failed ({type(exc).__name__})",
            )
        if not isinstance(inventory_snapshot, tuple) or len(inventory_snapshot) != 2:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                reason="inventory_snapshot must return a two-item tuple",
            )
        raw_snapshot, raw_capabilities = inventory_snapshot
        try:
            provider_snapshot = SourceSnapshot.model_validate(raw_snapshot)
        except (PydanticValidationError, TypeError) as exc:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                cause=exc,
                reason="provider returned an invalid source snapshot",
            )
    elif require_atomic:
        raise_mutation_error(
            "full_rebuild_required",
            "batch graph mutation requires AtomicCapabilityProvider.inventory_snapshot()",
            current_version=current_version,
        )
    else:
        raw_capabilities, provider_snapshot = await _load_compatible_inventory(provider)
    return _normalize_inventory(
        raw_capabilities,
        provider_snapshot=provider_snapshot,
        require_atomic=require_atomic,
    )


def load_sync_inventory(provider: Any) -> list[Fingerprint]:
    """Load a legacy synchronous provider for the status freshness check."""

    if (
        inspect.iscoroutinefunction(provider)
        or callable(getattr(provider, "inventory_snapshot", None))
        or callable(getattr(provider, "capabilities", None))
    ):
        raise RuntimeError("status() requires a synchronous capability_provider or an explicit expected_snapshot.")
    value = provider() if callable(provider) else provider
    if inspect.isawaitable(value):
        close = getattr(value, "close", None)
        if callable(close):
            close()
        raise RuntimeError("status() requires a synchronous capability_provider or an explicit expected_snapshot.")
    return [coerce_fingerprint(item) for item in value]


def validate_mutation_batch(
    changed_skill_ids: Sequence[str],
    *,
    request_id: str,
    source_revision: str,
) -> tuple[str, ...]:
    """Validate operation shape and return stable changed Skill IDs."""

    if not request_id or len(request_id) > 256:
        raise_mutation_error("invalid_request", "request_id must contain between 1 and 256 characters")
    if not source_revision or len(source_revision) > 512:
        raise_mutation_error("invalid_request", "source_revision must contain between 1 and 512 characters")
    if isinstance(changed_skill_ids, (str, bytes)):
        raise_mutation_error("invalid_request", "changed_skill_ids must be a sequence of Skill IDs")
    raw_ids = tuple(changed_skill_ids)
    if not raw_ids:
        raise_mutation_error("empty_batch", "a Skill graph mutation batch must not be empty")
    capability_ids: list[str] = []
    for item in raw_ids:
        if not isinstance(item, str) or not item.strip():
            raise_mutation_error("invalid_request", "changed_skill_ids must contain non-empty strings")
        capability_ids.append(item.strip())
    if len(capability_ids) != len(set(capability_ids)):
        raise_mutation_error(
            "duplicate_capability",
            "a Skill graph mutation batch must not contain duplicate capability IDs",
            affected_ids=tuple(sorted(set(capability_ids))),
        )
    return tuple(sorted(capability_ids))


def mutation_request_digest(
    operation: MutationOperation,
    affected_ids: tuple[str, ...],
    *,
    request_id: str,
    source_revision: str,
) -> str:
    """Return an order-independent digest for idempotency validation."""

    return stable_sha256(
        {
            "schema_version": "Symphony-skill-graph-mutation-request-v2",
            "request_id": request_id,
            "operation": operation,
            "source_revision": source_revision,
            "changed_skill_ids": list(affected_ids),
        }
    )


def artifact_identity_hashes(
    payload: dict[str, Any],
    *,
    current_version: str,
    affected_ids: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Read and validate mutation identity metadata from an active graph."""

    raw_content_hashes = payload.get("capability_hashes")
    raw_graph_hashes = payload.get("graph_identity_hashes")
    if not isinstance(raw_content_hashes, dict) or not isinstance(raw_graph_hashes, dict):
        raise_mutation_error(
            "full_rebuild_required",
            "the active graph predates mutation metadata; call build(force=True)",
            current_version=current_version,
            affected_ids=affected_ids,
        )
    content_hashes = {str(key): str(value or "") for key, value in raw_content_hashes.items()}
    graph_hashes = {str(key): str(value or "") for key, value in raw_graph_hashes.items()}
    if set(content_hashes) != set(graph_hashes) or not all(content_hashes.values()) or not all(graph_hashes.values()):
        raise_mutation_error(
            "artifact_invalid",
            "the active graph contains incomplete capability identity metadata",
            current_version=current_version,
            affected_ids=affected_ids,
        )
    if not str(payload.get("build_protocol_hash") or ""):
        raise_mutation_error(
            "full_rebuild_required",
            "the active graph has no build protocol identity; call build(force=True)",
            current_version=current_version,
            affected_ids=affected_ids,
        )
    return content_hashes, graph_hashes


def artifact_provider_snapshot(
    payload: dict[str, Any],
    *,
    current_version: str,
    affected_ids: tuple[str, ...],
) -> SourceSnapshot:
    """Read the provider snapshot that produced an active graph."""

    try:
        snapshot = SourceSnapshot.model_validate(payload.get("provider_source_snapshot"))
    except (PydanticValidationError, TypeError) as exc:
        raise_mutation_error(
            "full_rebuild_required",
            "the active graph has no atomic provider snapshot; call build(force=True)",
            current_version=current_version,
            affected_ids=affected_ids,
            cause=exc,
        )
    if not snapshot.content_hash.strip():
        raise_mutation_error(
            "full_rebuild_required",
            "the active graph provider snapshot has no content_hash; call build(force=True)",
            current_version=current_version,
            affected_ids=affected_ids,
        )
    return snapshot


def canonical_fingerprints_by_identity(
    fingerprints: Sequence[CapabilityFingerprint],
    *,
    current_version: str,
    affected_ids: tuple[str, ...],
) -> dict[str, CapabilityFingerprint]:
    """Index a target inventory after enforcing stable content identities."""

    output: dict[str, CapabilityFingerprint] = {}
    for item in fingerprints:
        fingerprint = CapabilityFingerprint.model_validate(item)
        if not fingerprint.content_hash.strip():
            raise_mutation_error(
                "source_snapshot_mismatch",
                f"target fingerprint {fingerprint.capability_id!r} has no content_hash",
                current_version=current_version,
                affected_ids=affected_ids,
            )
        key = capability_identity(fingerprint.capability_type, fingerprint.capability_id)
        if key in output:
            raise_mutation_error(
                "duplicate_capability",
                f"target inventory contains duplicate capability identity {key!r}",
                current_version=current_version,
                affected_ids=affected_ids,
            )
        output[key] = fingerprint
    return output


def validate_content_hash_identity(
    current_hashes: dict[str, str],
    current_graph_hashes: dict[str, str],
    target_hashes: dict[str, str],
    target_graph_hashes: dict[str, str],
    *,
    current_version: str,
    affected_ids: tuple[str, ...],
) -> None:
    """Reject graph changes hidden behind an unchanged provider content hash."""

    inconsistent = sorted(
        key
        for key in set(current_hashes) & set(target_hashes)
        if current_hashes[key] == target_hashes[key] and current_graph_hashes[key] != target_graph_hashes[key]
    )
    if inconsistent:
        raise_mutation_error(
            "source_snapshot_mismatch",
            "graph-affecting fields changed without a corresponding content_hash change",
            current_version=current_version,
            affected_ids=affected_ids,
            details={"inconsistent_capability_ids": [identity_capability_id(item) for item in inconsistent]},
            status=StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
        )


def inventory_changes(
    current_hashes: dict[str, str],
    target_hashes: dict[str, str],
) -> dict[str, set[str]]:
    """Calculate the exact provider-level add, update and delete sets."""

    current_keys = set(current_hashes)
    target_keys = set(target_hashes)
    return {
        "added": target_keys - current_keys,
        "updated": {key for key in current_keys & target_keys if current_hashes[key] != target_hashes[key]},
        "removed": current_keys - target_keys,
    }


def validate_mutation_against_inventory(
    operation: MutationOperation,
    requested_ids: tuple[str, ...],
    *,
    current_hashes: dict[str, str],
    target_by_identity: dict[str, CapabilityFingerprint],
    changes: dict[str, set[str]],
    current_version: str,
    affected_ids: tuple[str, ...],
) -> None:
    """Require the request batch to exactly equal the provider change set."""

    requested_keys = {capability_identity("skill", capability_id) for capability_id in requested_ids}
    current_keys = set(current_hashes)
    target_keys = set(target_by_identity)
    if operation == "add":
        _validate_add_membership(
            requested_keys,
            current_keys=current_keys,
            target_keys=target_keys,
            current_version=current_version,
            affected_ids=affected_ids,
        )
    elif operation == "update":
        _validate_update_membership(
            requested_keys,
            current_keys=current_keys,
            target_keys=target_keys,
            current_version=current_version,
            affected_ids=affected_ids,
        )
    else:
        _validate_delete_membership(
            requested_keys,
            current_keys=current_keys,
            target_keys=target_keys,
            current_version=current_version,
            affected_ids=affected_ids,
        )
    expected_change_name = {"add": "added", "update": "updated", "delete": "removed"}[operation]
    if operation == "update" and not any(changes.values()):
        return
    unrelated_changes = set().union(*(values for name, values in changes.items() if name != expected_change_name))
    if changes[expected_change_name] == requested_keys and not unrelated_changes:
        return
    raise_mutation_error(
        "change_set_mismatch",
        "the requested batch does not exactly describe the provider inventory change set",
        current_version=current_version,
        affected_ids=affected_ids,
        details={
            "requested_capability_ids": sorted(identity_capability_id(item) for item in requested_keys),
            "actual_added_ids": sorted(identity_capability_id(item) for item in changes["added"]),
            "actual_updated_ids": sorted(identity_capability_id(item) for item in changes["updated"]),
            "actual_removed_ids": sorted(identity_capability_id(item) for item in changes["removed"]),
        },
    )


def _validate_add_membership(
    requested_keys: set[str],
    *,
    current_keys: set[str],
    target_keys: set[str],
    current_version: str,
    affected_ids: tuple[str, ...],
) -> None:
    """Require added Skills to be absent from current and present in target."""

    existing = requested_keys & current_keys
    if existing:
        capability_id = identity_capability_id(min(existing))
        raise_mutation_error(
            "already_exists",
            f"Skill {capability_id!r} already exists in the active graph",
            current_version=current_version,
            affected_ids=affected_ids,
        )
    missing = requested_keys - target_keys
    if missing:
        raise_mutation_error(
            "change_set_mismatch",
            "added Skills are missing from the target inventory",
            current_version=current_version,
            affected_ids=affected_ids,
            details={"missing_target_ids": sorted(identity_capability_id(item) for item in missing)},
        )


def _validate_update_membership(
    requested_keys: set[str],
    *,
    current_keys: set[str],
    target_keys: set[str],
    current_version: str,
    affected_ids: tuple[str, ...],
) -> None:
    """Require updated Skills to exist in both current and target inventory."""

    missing_current = requested_keys - current_keys
    if missing_current:
        capability_id = identity_capability_id(min(missing_current))
        raise_mutation_error(
            "not_found",
            f"Skill {capability_id!r} does not exist in the active graph",
            current_version=current_version,
            affected_ids=affected_ids,
        )
    missing_target = requested_keys - target_keys
    if missing_target:
        raise_mutation_error(
            "change_set_mismatch",
            "updated Skills are missing from the target inventory",
            current_version=current_version,
            affected_ids=affected_ids,
            details={"missing_target_ids": sorted(identity_capability_id(item) for item in missing_target)},
        )


def _validate_delete_membership(
    requested_keys: set[str],
    *,
    current_keys: set[str],
    target_keys: set[str],
    current_version: str,
    affected_ids: tuple[str, ...],
) -> None:
    """Require deleted Skills to exist in current and be absent from target."""

    missing_current = requested_keys - current_keys
    if missing_current:
        capability_id = identity_capability_id(min(missing_current))
        raise_mutation_error(
            "not_found",
            f"Skill {capability_id!r} does not exist in the active graph",
            current_version=current_version,
            affected_ids=affected_ids,
        )
    still_present = requested_keys & target_keys
    if still_present:
        raise_mutation_error(
            "change_set_mismatch",
            "deleted Skills are still present in the target inventory",
            current_version=current_version,
            affected_ids=affected_ids,
            details={"present_target_ids": sorted(identity_capability_id(item) for item in still_present)},
        )


def graph_delta(
    current: dict[str, Any],
    target: dict[str, Any],
) -> GraphMutationDelta:
    """Summarize materialized node and edge changes."""

    current_nodes = {
        str(item.get("id") or ""): canonical_json(item)
        for item in current.get("nodes", [])
        if isinstance(item, dict) and item.get("id")
    }
    target_nodes = {
        str(item.get("id") or ""): canonical_json(item)
        for item in target.get("nodes", [])
        if isinstance(item, dict) and item.get("id")
    }
    current_edges = {canonical_json(item) for item in current.get("edges", []) if isinstance(item, dict)}
    target_edges = {canonical_json(item) for item in target.get("edges", []) if isinstance(item, dict)}
    return GraphMutationDelta(
        added_node_count=len(target_nodes.keys() - current_nodes.keys()),
        updated_node_count=sum(
            current_nodes[node_id] != target_nodes[node_id] for node_id in current_nodes.keys() & target_nodes.keys()
        ),
        removed_node_count=len(current_nodes.keys() - target_nodes.keys()),
        added_edge_count=len(target_edges - current_edges),
        removed_edge_count=len(current_edges - target_edges),
    )


def mutation_result_dict(result: GraphMutationResult) -> dict[str, Any]:
    """Serialize a public mutation result into a stable artifact record."""

    return {
        "request_id": result.request_id,
        "operation": result.operation,
        "status": result.status,
        "previous_version": result.previous_version,
        "version": result.version,
        "source_snapshot_id": result.source_snapshot_id,
        "changed_capability_ids": list(result.changed_capability_ids),
        "delta": {
            "added_node_count": result.delta.added_node_count,
            "updated_node_count": result.delta.updated_node_count,
            "removed_node_count": result.delta.removed_node_count,
            "added_edge_count": result.delta.added_edge_count,
            "removed_edge_count": result.delta.removed_edge_count,
        },
        "diagnostics": [dict(item) for item in result.diagnostics],
    }


def mutation_result_from_dict(value: Any) -> GraphMutationResult:
    """Validate and deserialize an idempotency record."""

    if not isinstance(value, dict):
        raise_mutation_error("artifact_invalid", "stored graph mutation result is invalid")
    try:
        operation = str(value["operation"])
        status = str(value["status"])
        if operation not in {"add", "update", "delete"} or status not in {"published", "noop"}:
            raise ValueError("unsupported mutation result enum")
        raw_delta = value.get("delta")
        raw_diagnostics = value.get("diagnostics") or []
        if not isinstance(raw_delta, dict):
            raise TypeError("delta must be an object")
        if not isinstance(raw_diagnostics, list) or not all(isinstance(item, dict) for item in raw_diagnostics):
            raise TypeError("diagnostics must be an object list")
        return GraphMutationResult(
            request_id=str(value["request_id"]),
            operation=operation,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            previous_version=str(value["previous_version"]),
            version=str(value["version"]),
            source_snapshot_id=str(value["source_snapshot_id"]),
            changed_capability_ids=tuple(str(item) for item in value.get("changed_capability_ids") or []),
            delta=GraphMutationDelta(
                added_node_count=int(raw_delta.get("added_node_count") or 0),
                updated_node_count=int(raw_delta.get("updated_node_count") or 0),
                removed_node_count=int(raw_delta.get("removed_node_count") or 0),
                added_edge_count=int(raw_delta.get("added_edge_count") or 0),
                removed_edge_count=int(raw_delta.get("removed_edge_count") or 0),
            ),
            diagnostics=tuple(dict(item) for item in raw_diagnostics),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise_mutation_error("artifact_invalid", "stored graph mutation result is invalid", cause=exc)


def coerce_canonical_fingerprint(value: Any) -> CapabilityFingerprint:
    """Coerce supported provider values to the canonical fingerprint model."""

    if isinstance(value, CapabilityFingerprint):
        return CapabilityFingerprint.model_validate(value)
    if isinstance(value, dict):
        return CapabilityFingerprint.model_validate(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return CapabilityFingerprint.model_validate(model_dump(mode="python"))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return CapabilityFingerprint.model_validate(to_dict())
    raise TypeError(f"Unsupported capability fingerprint: {type(value).__name__}")


def fingerprint_content_hash(fingerprint: Fingerprint) -> str:
    """Use provider content identity, with a deterministic legacy build fallback."""

    return fingerprint.content_hash.strip() or stable_sha256(fingerprint.to_internal_dict())


def build_protocol_hash(snapshot: dict[str, Any]) -> str:
    """Hash only graph-construction protocol fields, not inventory identity."""

    return stable_sha256(snapshot.get("symphony_graph_build") or {})


def mutation_version(source_snapshot: SourceSnapshot, request_digest: str) -> str:
    """Derive a retry-stable immutable artifact version."""

    source_digest = re.sub(r"[^A-Za-z0-9]+", "", source_snapshot.content_hash)[:16]
    return f"mutation-{source_digest}-{request_digest[:16]}"


def capability_identity(capability_type: str, capability_id: str) -> str:
    """Return a collision-free identity used by artifact metadata."""

    return f"{capability_type}:{capability_id}"


def snapshot_stability_payload(snapshot: SourceSnapshot) -> dict[str, Any]:
    """Ignore observation time while comparing provider snapshot identity."""

    return snapshot.model_dump(mode="json", exclude={"captured_at"})


def stable_sha256(value: Any) -> str:
    """Hash a JSON-compatible value with a canonical encoding."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


async def run_prepare_artifact(hook: Any, version_dir: Path) -> None:
    """Run an optional synchronous or asynchronous pre-publish hook."""

    if hook is None:
        return
    result = hook(version_dir)
    if inspect.isawaitable(result):
        await result


def raise_mutation_error(
    mutation_code: str,
    reason: str,
    *,
    current_version: str | None = None,
    affected_ids: Sequence[str] = (),
    details: dict[str, Any] | None = None,
    cause: BaseException | None = None,
    status: StatusCode = StatusCode.COMPONENT_SYMPHONY_BUILD_STATE_INVALID,
) -> NoReturn:
    """Raise a unified Symphony error with mutation recovery metadata."""

    error_details = {
        "mutation_code": mutation_code,
        "current_graph_version": current_version,
        "affected_capability_ids": list(affected_ids),
        **(details or {}),
    }
    raise_error(status, reason=reason, details=error_details, cause=cause)
    raise AssertionError("unreachable")


async def _load_compatible_inventory(provider: Any) -> tuple[Any, SourceSnapshot | None]:
    capabilities_loader = getattr(provider, "capabilities", None)
    snapshot_loader = getattr(provider, "source_snapshot", None)
    if not callable(capabilities_loader):
        raw_capabilities = provider() if callable(provider) else provider
        if inspect.isawaitable(raw_capabilities):
            raw_capabilities = await raw_capabilities
        return raw_capabilities, None
    try:
        snapshot_before = await snapshot_loader() if callable(snapshot_loader) else None
        raw_capabilities = await capabilities_loader()
        snapshot_after = await snapshot_loader() if callable(snapshot_loader) else snapshot_before
        if snapshot_before is None:
            return raw_capabilities, None
        provider_snapshot = SourceSnapshot.model_validate(snapshot_before)
        validated_after = SourceSnapshot.model_validate(snapshot_after)
        if snapshot_stability_payload(provider_snapshot) != snapshot_stability_payload(validated_after):
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                reason="provider snapshot changed while the capability inventory was being read",
            )
        return raw_capabilities, provider_snapshot
    except BaseError:
        raise
    except Exception as exc:  # noqa: BLE001 -- providers expose application-specific failures.
        raise_error(
            StatusCode.COMPONENT_SYMPHONY_BUILD_RUNTIME_ERROR,
            cause=exc,
            reason=f"capability provider failed ({type(exc).__name__})",
        )
        raise AssertionError("unreachable") from exc


def _normalize_inventory(
    raw_capabilities: Any,
    *,
    provider_snapshot: SourceSnapshot | None,
    require_atomic: bool,
) -> tuple[list[Fingerprint], SourceSnapshot | None, list[CapabilityFingerprint] | None]:
    try:
        raw_items = list(raw_capabilities)
        capabilities = [coerce_fingerprint(item) for item in raw_items]
    except (TypeError, ValueError) as exc:
        raise_error(
            StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
            cause=exc,
            reason="provider returned an invalid capability inventory",
        )
    identities = [item.id for item in capabilities]
    if len(identities) != len(set(identities)):
        raise_error(
            StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
            reason="capability_id must be unique within a provider snapshot",
        )
    if (
        provider_snapshot is not None
        and provider_snapshot.capability_count is not None
        and provider_snapshot.capability_count != len(capabilities)
    ):
        raise_error(
            StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
            reason="source snapshot capability_count does not match the inventory",
        )
    canonical: list[CapabilityFingerprint] | None = None
    try:
        canonical = [coerce_canonical_fingerprint(item) for item in raw_items]
    except (PydanticValidationError, TypeError, ValueError):
        if require_atomic:
            raise_error(
                StatusCode.COMPONENT_SYMPHONY_INVENTORY_INVALID,
                reason="atomic inventory must contain complete CapabilityFingerprint values",
            )
    return capabilities, provider_snapshot, canonical


def identity_capability_id(identity: str) -> str:
    """Return the capability ID encoded in an internal identity key."""

    return identity.split(":", 1)[-1]


def canonical_json(value: Any) -> str:
    """Serialize a value deterministically for hashing and equality checks."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
