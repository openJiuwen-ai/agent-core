# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure minimal point-edge execution-graph contracts.

This module deliberately does not read a planned graph while deciding observed
execution edges.  Capability identities must come from an invoke-start snapshot
provided by the host integration.  Rail scheduling, sink error isolation,
persistence, retries, and revisions belong elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable
from unicodedata import category as unicode_category

from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    SymphonyEdgeDecision,
    SymphonyInterruptContinuation,
)
from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    _valid_interrupt_continuations as _unambiguous_interrupt_continuations,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    SymphonyExecutionFragment,
)

CapabilityType = Literal["skill", "tool", "subagent"]
ExecutionOutcome = Literal["success", "failed", "partial"]

_CAPABILITY_TYPES = frozenset({"skill", "tool", "subagent"})
_OUTCOMES = frozenset({"success", "failed", "partial"})
_METHOD_STRENGTH = frozenset({("model_assisted", "low")})
_EVIDENCE_REF_RE = re.compile(r"^(?P<trace>[^#\s]+)#span=(?P<span>[^#\s]+)$")
_MAX_JSON_DEPTH = 128


@dataclass(frozen=True)
class CapabilityIdentity:
    """One immutable capability alias captured at invoke start.

    Runtime validation intentionally happens in the pure graph builder so a
    malformed provider record drops affected observations instead of raising in
    the user-task path.
    """

    capability_id: str
    capability_type: CapabilityType
    capability_name: str


@runtime_checkable
class CapabilitySnapshotProvider(Protocol):
    """Synchronously freeze capability aliases visible to one invocation."""

    def snapshot_capabilities(self) -> Sequence[CapabilityIdentity]:
        """Return the immutable identities visible at invoke start."""

        ...


def build_symphony_execution_graph(
    *,
    trace_id: str,
    query: str,
    outcome: ExecutionOutcome,
    candidates: Sequence[SymphonyEdgeCandidate],
    decisions: Sequence[SymphonyEdgeDecision],
    capability_snapshot: Sequence[CapabilityIdentity],
    reason: str | None = None,
    quality_flags: Sequence[str] = (),
    graph_snapshot: Mapping[str, Any] | None = None,
    trace_ids: Sequence[str] = (),
    interrupt_continuations: Sequence[SymphonyInterruptContinuation] = (),
) -> dict[str, Any]:
    """Build a deterministic JGF execution graph from observed edge decisions.

    Invalid or ambiguous identities and malformed candidate/decision contracts
    fail closed by dropping only the observations that depend on them.  Invalid
    top-level envelope fields return an empty mapping because no valid JGF
    envelope can be represented.
    """

    normalized_trace_id = _validated_trace_id(trace_id)
    if normalized_trace_id is None or not isinstance(query, str):
        return {}
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        return {}
    normalized_reason = _nonempty_text(reason)
    if outcome in {"failed", "partial"} and normalized_reason is None:
        return {}
    normalized_trace_ids = _normalized_trace_ids(normalized_trace_id, trace_ids)
    if normalized_trace_ids is None:
        return {}

    try:
        identity_index = _IdentityIndex(capability_snapshot)
        candidate_index = _unique_candidates(candidates)
        decision_index = _unique_decisions(decisions)
    except MemoryError:
        raise
    except Exception:
        identity_index = _IdentityIndex(())
        candidate_index = {}
        decision_index = {}

    edges: list[dict[str, Any]] = []
    endpoint_identities: dict[str, CapabilityIdentity] = {}
    valid_trace_ids = frozenset(normalized_trace_ids)
    valid_continuations = frozenset(
        item
        for item in _valid_interrupt_continuations(interrupt_continuations)
        if item.trace_ids == normalized_trace_ids[: len(item.trace_ids)]
    )
    for candidate_id in sorted(candidate_index.keys() & decision_index.keys()):
        candidate = candidate_index.get(candidate_id)
        decision = decision_index.get(candidate_id)
        if candidate is None or decision is None:
            continue
        observation = _safe_validated_observation(
            normalized_trace_id,
            valid_trace_ids,
            valid_continuations,
            candidate,
            decision,
            identity_index,
        )
        if observation is None:
            continue
        edge, source_identity, target_identity = observation
        edges.append(edge)
        endpoint_identities[source_identity.capability_id] = source_identity
        endpoint_identities[target_identity.capability_id] = target_identity

    edges = _deduplicate_edges(edges)
    nodes = {
        capability_id: {
            "label": identity.capability_type,
        }
        for capability_id, identity in sorted(endpoint_identities.items())
    }
    graph_without_id: dict[str, Any] = {
        "type": "execution_graph",
        "label": "capability execution graph",
        "directed": True,
        "nodes": nodes,
        "edges": edges,
    }
    envelope_for_id: dict[str, Any] = {
        "trace_id": normalized_trace_id,
        "query": query,
        "outcome": outcome,
        "graph": graph_without_id,
    }
    if len(normalized_trace_ids) > 1:
        envelope_for_id["trace_ids"] = list(normalized_trace_ids)
    if outcome in {"failed", "partial"}:
        envelope_for_id["reason"] = normalized_reason
    flags = _normalized_quality_flags(quality_flags)
    if flags:
        envelope_for_id["quality_flags"] = list(flags)
    if graph_snapshot is not None:
        normalized_snapshot = _normalized_graph_snapshot(graph_snapshot)
        if normalized_snapshot is None:
            return {}
        envelope_for_id["graph_snapshot"] = normalized_snapshot
    try:
        graph_id = _execution_graph_id(envelope_for_id)
    except (TypeError, ValueError):
        return {}

    result = dict(envelope_for_id)
    result["graph"] = {
        "id": graph_id,
        **graph_without_id,
    }
    return result


@dataclass(frozen=True)
class _IdentityIndex:
    by_alias: dict[tuple[str, str], CapabilityIdentity]
    ambiguous_ids: frozenset[str]

    def __init__(self, identities: Sequence[CapabilityIdentity]) -> None:
        try:
            supplied_identities = tuple(identities)
        except MemoryError:
            raise
        except Exception:
            supplied_identities = ()

        record_validity: dict[int, bool] = {}
        capability_ids_by_index: dict[int, str | None] = {}
        records_by_id: dict[str, list[int]] = defaultdict(list)
        records_by_alias: dict[tuple[str, str], list[int]] = defaultdict(list)
        snapshot_aliases_readable = True
        for index, identity in enumerate(supplied_identities):
            if not isinstance(identity, CapabilityIdentity):
                snapshot_aliases_readable = False
                break
            try:
                capability_id = _raw_identity_text(identity, "capability_id")
                capability_type = _raw_identity_text(identity, "capability_type")
                capability_name = _raw_identity_text(identity, "capability_name")
            except MemoryError:
                raise
            except Exception:
                snapshot_aliases_readable = False
                break
            try:
                is_valid = _valid_identity(
                    identity,
                    capability_id=capability_id,
                    capability_type=capability_type,
                    capability_name=capability_name,
                )
            except MemoryError:
                raise
            except Exception:
                is_valid = False
            record_validity[index] = is_valid
            capability_ids_by_index[index] = capability_id
            if capability_id is not None:
                records_by_id[capability_id].append(index)
            if capability_type is not None:
                aliases: set[tuple[str, str]] = set()
                if capability_name is not None:
                    aliases.add((capability_type, capability_name))
                if capability_id is not None:
                    aliases.add((capability_type, capability_id))
                for alias in aliases:
                    records_by_alias[alias].append(index)

        if not snapshot_aliases_readable:
            object.__setattr__(self, "by_alias", {})
            object.__setattr__(self, "ambiguous_ids", frozenset())
            return

        ambiguous_ids = frozenset(
            capability_id
            for capability_id, record_indexes in records_by_id.items()
            if len(record_indexes) != 1 or not all(record_validity[index] for index in record_indexes)
        )
        by_alias: dict[tuple[str, str], CapabilityIdentity] = {}
        for alias, record_indexes in records_by_alias.items():
            if len(record_indexes) != 1:
                continue
            index = record_indexes[0]
            identity = supplied_identities[index]
            if not record_validity[index] or not isinstance(identity, CapabilityIdentity):
                continue
            capability_id = capability_ids_by_index[index]
            if capability_id is None or capability_id in ambiguous_ids:
                continue
            by_alias[alias] = identity

        object.__setattr__(self, "by_alias", by_alias)
        object.__setattr__(self, "ambiguous_ids", ambiguous_ids)

    def resolve(self, fragment: SymphonyExecutionFragment) -> CapabilityIdentity | None:
        capability_type = fragment.capability_type
        capability_name = fragment.capability_name
        if (
            not isinstance(capability_type, str)
            or capability_type not in _CAPABILITY_TYPES
            or _nonempty_text(capability_name) is None
        ):
            return None
        identity = self.by_alias.get((capability_type, capability_name))
        if identity is None:
            return None
        return identity if identity.capability_id not in self.ambiguous_ids else None


def _unique_candidates(candidates: Sequence[SymphonyEdgeCandidate]) -> dict[str, SymphonyEdgeCandidate]:
    try:
        items = tuple(candidates)
    except MemoryError:
        raise
    except Exception:
        return {}
    valid_items: list[tuple[str, SymphonyEdgeCandidate]] = []
    for item in items:
        if not isinstance(item, SymphonyEdgeCandidate):
            continue
        candidate_id = _safe_candidate_id(item)
        if candidate_id is not None:
            valid_items.append((candidate_id, item))
    counts = Counter(candidate_id for candidate_id, _ in valid_items)
    return {candidate_id: item for candidate_id, item in valid_items if counts[candidate_id] == 1}


def _unique_decisions(decisions: Sequence[SymphonyEdgeDecision]) -> dict[str, SymphonyEdgeDecision]:
    try:
        items = tuple(decisions)
    except MemoryError:
        raise
    except Exception:
        return {}
    valid_items: list[tuple[str, SymphonyEdgeDecision]] = []
    for item in items:
        if not isinstance(item, SymphonyEdgeDecision):
            continue
        candidate_id = _safe_candidate_id(item)
        if candidate_id is not None:
            valid_items.append((candidate_id, item))
    counts = Counter(candidate_id for candidate_id, _ in valid_items)
    return {candidate_id: item for candidate_id, item in valid_items if counts[candidate_id] == 1}


def _safe_candidate_id(item: SymphonyEdgeCandidate | SymphonyEdgeDecision) -> str | None:
    try:
        candidate_id = item.candidate_id
    except MemoryError:
        raise
    except Exception:
        return None
    return _nonempty_text(candidate_id)


def _safe_validated_observation(
    trace_id: str,
    trace_ids: frozenset[str],
    interrupt_continuations: frozenset[SymphonyInterruptContinuation],
    candidate: SymphonyEdgeCandidate,
    decision: SymphonyEdgeDecision,
    identity_index: _IdentityIndex,
) -> tuple[dict[str, Any], CapabilityIdentity, CapabilityIdentity] | None:
    try:
        return _validated_observation(trace_id, trace_ids, interrupt_continuations, candidate, decision, identity_index)
    except MemoryError:
        raise
    except Exception:
        return None


def _validated_observation(
    trace_id: str,
    trace_ids: frozenset[str],
    interrupt_continuations: frozenset[SymphonyInterruptContinuation],
    candidate: SymphonyEdgeCandidate,
    decision: SymphonyEdgeDecision,
    identity_index: _IdentityIndex,
) -> tuple[dict[str, Any], CapabilityIdentity, CapabilityIdentity] | None:
    source = candidate.source_fragment
    target = candidate.target_fragment
    if not _valid_fragment(source, trace_ids) or not _valid_fragment(target, trace_ids):
        return None
    if source.continuity_index != target.continuity_index or _fragment_occurrence_id(source) == _fragment_occurrence_id(
        target
    ):
        return None
    if source.trace_id != target.trace_id:
        continuation = candidate.interrupt_continuation
        if (
            continuation is None
            or continuation not in interrupt_continuations
            or continuation.source_trace_id != source.trace_id
            or continuation.target_trace_id != target.trace_id
            or continuation.continuity_index != source.continuity_index
        ):
            return None
    if _nonempty_text(decision.candidate_id) is None or decision.candidate_id != candidate.candidate_id:
        return None
    if decision.source_fragment_id != source.fragment_id or decision.target_fragment_id != target.fragment_id:
        return None
    if not isinstance(decision.status, str) or decision.status not in {"success", "failure"}:
        return None
    if not isinstance(decision.evidence_method, str) or not isinstance(decision.evidence_strength, str):
        return None
    if (decision.evidence_method, decision.evidence_strength) not in _METHOD_STRENGTH:
        return None

    allowed_spans_by_trace = {
        source.trace_id: frozenset(source.span_ids)
        | (frozenset(target.span_ids) if source.trace_id == target.trace_id else frozenset()),
        target.trace_id: frozenset(target.span_ids)
        | (frozenset(source.span_ids) if source.trace_id == target.trace_id else frozenset()),
    }
    candidate_refs = _validated_evidence_refs(candidate.evidence_refs, allowed_spans_by_trace)
    decision_refs = _validated_evidence_refs(decision.evidence_refs, allowed_spans_by_trace)
    if candidate_refs is None or decision_refs is None:
        return None
    if len(decision_refs) < 2 or not set(decision_refs).issubset(candidate_refs):
        return None
    anchor_refs = {f"{fragment.trace_id}#span={fragment.anchor_span_id}" for fragment in (source, target)}
    if not anchor_refs.issubset(decision_refs):
        return None
    if decision.status == "failure" and _nonempty_text(decision.reason) is None:
        return None

    source_identity = identity_index.resolve(source)
    target_identity = identity_index.resolve(target)
    if source_identity is None or target_identity is None:
        return None
    edge = {
        "source": source_identity.capability_id,
        "target": target_identity.capability_id,
        "relation": "can_feed",
        "metadata": {"success": decision.status == "success"},
    }
    return edge, source_identity, target_identity


def _deduplicate_edges(edges: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {
        (
            str(edge["source"]),
            str(edge["target"]),
            str(edge["relation"]),
            bool(edge["metadata"]["success"]),
        ): edge
        for edge in edges
    }
    return [unique[key] for key in sorted(unique)]


def _valid_fragment(fragment: Any, trace_ids: frozenset[str]) -> bool:
    span_ids = _fragment_span_ids(fragment)
    return (
        isinstance(fragment, SymphonyExecutionFragment)
        and isinstance(fragment.capability_type, str)
        and fragment.capability_type in _CAPABILITY_TYPES
        and _nonempty_text(fragment.capability_name) is not None
        and _nonempty_text(fragment.fragment_id) is not None
        and _nonempty_text(fragment.anchor_span_id) is not None
        and _nonempty_text(fragment.branch_span_id) is not None
        and isinstance(fragment.continuity_index, int)
        and not isinstance(fragment.continuity_index, bool)
        and _validated_trace_id(fragment.trace_id) is not None
        and fragment.trace_id in trace_ids
        and span_ids is not None
        and fragment.anchor_span_id in span_ids
    )


def _valid_identity(
    identity: Any,
    *,
    capability_id: str | None,
    capability_type: str | None,
    capability_name: str | None,
) -> bool:
    return (
        isinstance(identity, CapabilityIdentity)
        and capability_type in _CAPABILITY_TYPES
        and _valid_identity_text(capability_id)
        and _valid_identity_text(capability_name)
    )


def _valid_identity_text(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(unicode_category(character) in {"Cc", "Cf"} for character in value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _raw_identity_text(identity: Any, field_name: str) -> str | None:
    if not isinstance(identity, CapabilityIdentity):
        return None
    value = getattr(identity, field_name, None)
    return value if isinstance(value, str) and value.strip() else None


def _fragment_span_ids(fragment: Any) -> frozenset[str] | None:
    if not isinstance(fragment, SymphonyExecutionFragment):
        return None
    span_ids = fragment.span_ids
    if not isinstance(span_ids, tuple):
        return None
    if not span_ids or any(_nonempty_text(span_id) is None for span_id in span_ids):
        return None
    return frozenset(span_ids)


def _fragment_occurrence_id(fragment: SymphonyExecutionFragment) -> tuple[str, int, str]:
    return fragment.trace_id, fragment.continuity_index, fragment.anchor_span_id


def _validated_evidence_refs(
    refs: Any,
    allowed_spans_by_trace: Mapping[str, frozenset[str]],
) -> tuple[str, ...] | None:
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        return None
    normalized: set[str] = set()
    for ref in refs:
        if not isinstance(ref, str):
            return None
        match = _EVIDENCE_REF_RE.fullmatch(ref)
        if match is None:
            return None
        matched_trace_id = match.group("trace")
        if _validated_trace_id(matched_trace_id) is None:
            return None
        allowed_span_ids = allowed_spans_by_trace.get(matched_trace_id)
        if allowed_span_ids is None or match.group("span") not in allowed_span_ids:
            return None
        normalized.add(ref)
    return tuple(sorted(normalized))


def _nonempty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalized_quality_flags(flags: Any) -> tuple[str, ...]:
    if isinstance(flags, (str, bytes)) or not isinstance(flags, Sequence):
        return ()
    try:
        normalized = {value.strip() for value in flags if isinstance(value, str) and value.strip()}
    except MemoryError:
        raise
    except Exception:
        return ()
    return tuple(sorted(normalized))


def _validated_trace_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "#" in value:
        return None
    if any(character.isspace() or unicode_category(character) == "Cc" for character in value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    return value


def _normalized_trace_ids(trace_id: str, trace_ids: Sequence[str]) -> tuple[str, ...] | None:
    if isinstance(trace_ids, (str, bytes)):
        return None
    try:
        supplied = tuple(trace_ids)
    except MemoryError:
        raise
    except Exception:
        return None
    normalized = [trace_id]
    for value in supplied:
        valid = _validated_trace_id(value)
        if valid is None:
            return None
        if valid not in normalized:
            normalized.append(valid)
    return tuple(normalized)


def _valid_interrupt_continuations(
    continuations: Sequence[SymphonyInterruptContinuation],
) -> tuple[SymphonyInterruptContinuation, ...]:
    if isinstance(continuations, (str, bytes)):
        return ()
    try:
        items = _unambiguous_interrupt_continuations(continuations)
    except MemoryError:
        raise
    except Exception:
        return ()
    return tuple(
        item
        for item in items
        if isinstance(item, SymphonyInterruptContinuation)
        and _validated_trace_id(item.source_trace_id) is not None
        and _validated_trace_id(item.target_trace_id) is not None
        and item.source_trace_id != item.target_trace_id
        and isinstance(item.continuity_index, int)
        and not isinstance(item.continuity_index, bool)
        and item.continuity_index >= 0
        and isinstance(item.source_segment_index, int)
        and not isinstance(item.source_segment_index, bool)
        and item.source_segment_index >= 0
        and isinstance(item.target_segment_index, int)
        and not isinstance(item.target_segment_index, bool)
        and item.target_segment_index >= 0
        and item.target_segment_index == item.source_segment_index + 1
        and isinstance(item.trace_ids, tuple)
        and len(item.trace_ids) > item.target_segment_index
        and all(_validated_trace_id(trace_id) is not None for trace_id in item.trace_ids)
        and item.trace_ids[item.source_segment_index] == item.source_trace_id
        and item.trace_ids[item.target_segment_index] == item.target_trace_id
    )


def _normalized_graph_snapshot(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    output: dict[str, str] = {}
    for field_name in ("static_revision", "observation_revision"):
        normalized = _nonempty_text(value.get(field_name))
        if normalized is None:
            return None
        output[field_name] = normalized
    merged_revision = value.get("merged_revision")
    if merged_revision is not None:
        normalized = _nonempty_text(merged_revision)
        if normalized is None:
            return None
        output["merged_revision"] = normalized
    return output


def _canonical_graph_pair(
    planned_graph: dict[str, Any] | None,
    execution_graph: dict[str, Any],
) -> str:
    try:
        if planned_graph is not None and not isinstance(planned_graph, dict):
            raise ValueError("planned_graph must be a JSON object or None")
        if not isinstance(execution_graph, dict):
            raise ValueError("execution_graph must be a JSON object")
        normalized_planned = _normalize_json_value(planned_graph)
        normalized_execution = _normalize_json_value(execution_graph)
        _validate_execution_envelope(normalized_execution)
        if normalized_planned is not None:
            _validate_planned_envelope(normalized_planned)
        return _canonical_json(
            {
                "planned_graph": normalized_planned,
                "execution_graph": normalized_execution,
            }
        )
    except MemoryError:
        raise
    except Exception as exc:
        raise ValueError("invalid Symphony graph submission") from exc


def _normalize_json_value(value: Any) -> Any:
    try:
        _validate_json_value(value)
        canonical = _canonical_json(value)
        canonical.encode("utf-8")
        return json.loads(canonical)
    except MemoryError:
        raise
    except Exception as exc:
        raise ValueError("graph pair must contain strict JSON values") from exc


def _validate_json_value(
    value: Any,
    *,
    depth: int = 0,
    ancestors: set[int] | None = None,
) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("JSON nesting exceeds the supported depth")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("non-finite numbers are not valid JSON")
    if isinstance(value, list):
        _validate_json_container(value, depth, ancestors)
        return
    if isinstance(value, dict):
        _validate_json_container(value, depth, ancestors)
        return
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")


def _validate_json_container(
    value: list[Any] | dict[Any, Any],
    depth: int,
    ancestors: set[int] | None,
) -> None:
    active = set() if ancestors is None else ancestors
    marker = id(value)
    if marker in active:
        raise ValueError("circular JSON containers are not supported")
    active.add(marker)
    try:
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be strings")
                _validate_json_value(item, depth=depth + 1, ancestors=active)
        else:
            for item in value:
                _validate_json_value(item, depth=depth + 1, ancestors=active)
    finally:
        active.remove(marker)


def _validate_execution_envelope(envelope: Any) -> None:
    if not isinstance(envelope, Mapping):
        raise ValueError("execution_graph must be an object")
    trace_id = envelope.get("trace_id")
    if _validated_trace_id(trace_id) is None:
        raise ValueError("execution_graph.trace_id is invalid")
    query = envelope.get("query")
    if not isinstance(query, str):
        raise ValueError("execution_graph.query must be a string")
    outcome = envelope.get("outcome")
    if outcome not in _OUTCOMES:
        raise ValueError("execution_graph.outcome is invalid")
    if outcome == "success":
        if "reason" in envelope:
            raise ValueError("successful execution_graph must omit reason")
    elif _nonempty_text(envelope.get("reason")) is None:
        raise ValueError("failed or partial execution_graph requires reason")
    if "quality_flags" in envelope:
        flags = envelope["quality_flags"]
        if (
            not isinstance(flags, list)
            or any(_nonempty_text(flag) is None for flag in flags)
            or flags != sorted(set(flags))
        ):
            raise ValueError("execution_graph.quality_flags must be sorted unique strings")
    if "graph_snapshot" in envelope and _normalized_graph_snapshot(envelope["graph_snapshot"]) is None:
        raise ValueError("execution_graph.graph_snapshot is invalid")
    trace_ids = envelope.get("trace_ids")
    if trace_ids is not None:
        if (
            not isinstance(trace_ids, list)
            or len(trace_ids) < 2
            or trace_ids[0] != trace_id
            or len(set(trace_ids)) != len(trace_ids)
            or any(_validated_trace_id(item) is None for item in trace_ids)
        ):
            raise ValueError("execution_graph.trace_ids is invalid")

    graph = envelope.get("graph")
    nodes, edges = _validate_graph_shell(graph, "execution_graph")
    _validate_graph_nodes(nodes, execution=True)
    edge_identities: set[tuple[str, str, str, bool]] = set()
    for edge in edges:
        metadata = _validate_graph_edge(edge, nodes)
        _validate_execution_edge_metadata(metadata)
        edge_identity = (
            edge["source"],
            edge["target"],
            edge["relation"],
            metadata["success"],
        )
        if edge_identity in edge_identities:
            raise ValueError("duplicate execution edge identity")
        edge_identities.add(edge_identity)

    graph_without_id = dict(graph)
    graph_without_id.pop("id", None)
    envelope_without_graph_id = dict(envelope)
    envelope_without_graph_id["graph"] = graph_without_id
    if graph.get("id") != _execution_graph_id(envelope_without_graph_id):
        raise ValueError("execution graph.id does not match its content")


def _validate_planned_envelope(envelope: Any) -> None:
    if not isinstance(envelope, Mapping):
        raise ValueError("planned_graph must be an object")
    graph = envelope.get("graph")
    nodes, edges = _validate_graph_shell(graph, "planned_graph")
    metadata = graph.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("status") != "ready":
        raise ValueError("planned_graph must have ready metadata")
    _validate_graph_nodes(nodes, execution=False)
    for edge in edges:
        _validate_graph_edge(edge, nodes)


def _validate_graph_shell(
    graph: Any,
    expected_type: str,
) -> tuple[Mapping[str, Any], list[Any]]:
    if not isinstance(graph, Mapping):
        raise ValueError(f"{expected_type}.graph must be an object")
    if _nonempty_text(graph.get("id")) is None:
        raise ValueError(f"{expected_type}.graph.id is required")
    if graph.get("type") != expected_type:
        raise ValueError(f"graph.type must be {expected_type}")
    if graph.get("directed") is not True:
        raise ValueError("graph.directed must be true")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, Mapping):
        raise ValueError("graph.nodes must be an object")
    if not isinstance(edges, list):
        raise ValueError("graph.edges must be a list")
    return nodes, edges


def _validate_graph_nodes(nodes: Mapping[str, Any], *, execution: bool) -> None:
    for node_id, node in nodes.items():
        if _nonempty_text(node_id) is None or not isinstance(node, Mapping):
            raise ValueError("graph node IDs and payloads must be valid")
        if not execution:
            continue
        if node.get("label") not in _CAPABILITY_TYPES:
            raise ValueError("execution node label must be a capability type")
        if set(node) != {"label"}:
            raise ValueError("execution nodes may only contain label")


def _validate_graph_edge(edge: Any, nodes: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(edge, Mapping):
        raise ValueError("graph edge must be an object")
    source = edge.get("source")
    target = edge.get("target")
    if _nonempty_text(source) is None or _nonempty_text(target) is None:
        raise ValueError("graph edge endpoints are required")
    if source not in nodes or target not in nodes:
        raise ValueError("graph edge endpoints must exist in nodes")
    if edge.get("relation") != "can_feed":
        raise ValueError("graph edge relation must be can_feed")
    metadata = edge.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("graph edge metadata must be an object")
    return metadata


def _validate_execution_edge_metadata(metadata: Mapping[str, Any]) -> None:
    if set(metadata) - {"success", "failure_domain"}:
        raise ValueError("execution edge metadata contains unsupported fields")
    success = metadata.get("success")
    if not isinstance(success, bool):
        raise ValueError("execution edge success must be boolean")
    failure_domain = metadata.get("failure_domain")
    if failure_domain is not None and (success or _nonempty_text(failure_domain) is None):
        raise ValueError("execution edge failure_domain is invalid")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _execution_graph_id(envelope_without_graph_id: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(envelope_without_graph_id).encode("utf-8")).hexdigest()
    return f"execution_graph:sha256:{digest}"


__all__ = [
    "CapabilityIdentity",
    "CapabilitySnapshotProvider",
    "build_symphony_execution_graph",
]
