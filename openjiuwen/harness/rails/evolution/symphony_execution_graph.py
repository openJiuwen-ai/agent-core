# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure SDD-0006 execution-graph and paired-submission contracts.

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
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from unicodedata import category as unicode_category

from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    SymphonyEdgeDecision,
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
    """One immutable capability identity captured at invoke start.

    Runtime validation intentionally happens in the pure graph builder so a
    malformed provider record drops affected observations instead of raising in
    the user-task path.
    """

    capability_id: str
    capability_type: CapabilityType
    capability_name: str
    version: str
    content_hash: str
    input_ports: tuple[str, ...]
    output_ports: tuple[str, ...]


@runtime_checkable
class CapabilitySnapshotProvider(Protocol):
    """Synchronously freeze identities from one active artifact version."""

    def snapshot_capabilities(self) -> Sequence[CapabilityIdentity]:
        """Return the immutable identities visible at invoke start."""

        ...


@dataclass(frozen=True, init=False, slots=True)
class SymphonyGraphEvolutionSubmission:
    """Deeply immutable canonical planned/execution graph pair.

    ``planned_graph`` and ``execution_graph`` return detached JSON-compatible
    views on every read.  Mutating a returned mapping or nested list therefore
    cannot change later reads or invalidate ``submission_id``.
    """

    submission_id: str
    _canonical_pair_json: str = field(repr=False)

    def __init__(
        self,
        planned_graph: dict[str, Any] | None,
        execution_graph: dict[str, Any],
    ) -> None:
        canonical_pair = _canonical_graph_pair(planned_graph, execution_graph)
        object.__setattr__(
            self,
            "submission_id",
            f"sha256:{hashlib.sha256(canonical_pair.encode('utf-8')).hexdigest()}",
        )
        object.__setattr__(self, "_canonical_pair_json", canonical_pair)

    @property
    def planned_graph(self) -> dict[str, Any] | None:
        """Return a detached planned-graph JSON view, if one was captured."""

        return json.loads(self._canonical_pair_json)["planned_graph"]

    @property
    def execution_graph(self) -> dict[str, Any]:
        """Return a detached execution-graph JSON view."""

        return json.loads(self._canonical_pair_json)["execution_graph"]

    def canonical_pair_json(self) -> str:
        """Return the immutable canonical pair JSON used for hashing."""

        return self._canonical_pair_json

    def canonical_pair_bytes(self) -> bytes:
        """Return the canonical UTF-8 bytes used to derive ``submission_id``."""

        return self._canonical_pair_json.encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached sink payload; do not JSON-encode this object directly."""

        pair = json.loads(self._canonical_pair_json)
        return {
            "submission_id": self.submission_id,
            "planned_graph": pair["planned_graph"],
            "execution_graph": pair["execution_graph"],
        }


@runtime_checkable
class SymphonyGraphObservationSink(Protocol):
    """Asynchronous sink contract; Rail owns failure isolation."""

    async def submit(self, submission: SymphonyGraphEvolutionSubmission) -> None:
        """Accept one completed graph-evolution submission."""

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
    for candidate_id in sorted(candidate_index.keys() & decision_index.keys()):
        candidate = candidate_index.get(candidate_id)
        decision = decision_index.get(candidate_id)
        if candidate is None or decision is None:
            continue
        observation = _safe_validated_observation(
            normalized_trace_id,
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

    edges.sort(
        key=lambda edge: (
            edge["source"],
            edge["target"],
            edge["metadata"]["candidate_id"],
            edge["metadata"]["source_fragment_id"],
            edge["metadata"]["target_fragment_id"],
        )
    )
    nodes = {
        capability_id: {
            "label": identity.capability_type,
            "metadata": {
                "capability_type": identity.capability_type,
                "version": identity.version,
                "content_hash": identity.content_hash,
                "input_ports": list(identity.input_ports),
                "output_ports": list(identity.output_ports),
            },
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
    if outcome in {"failed", "partial"}:
        envelope_for_id["reason"] = normalized_reason
    flags = _normalized_quality_flags(quality_flags)
    if flags:
        envelope_for_id["quality_flags"] = list(flags)
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


def build_symphony_graph_evolution_submission(
    planned_graph: dict[str, Any] | None,
    execution_graph: dict[str, Any],
) -> SymphonyGraphEvolutionSubmission:
    """Build a deeply immutable pair with a canonical SHA-256 identity."""

    return SymphonyGraphEvolutionSubmission(planned_graph, execution_graph)


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
    candidate: SymphonyEdgeCandidate,
    decision: SymphonyEdgeDecision,
    identity_index: _IdentityIndex,
) -> tuple[dict[str, Any], CapabilityIdentity, CapabilityIdentity] | None:
    try:
        return _validated_observation(trace_id, candidate, decision, identity_index)
    except MemoryError:
        raise
    except Exception:
        return None


def _validated_observation(
    trace_id: str,
    candidate: SymphonyEdgeCandidate,
    decision: SymphonyEdgeDecision,
    identity_index: _IdentityIndex,
) -> tuple[dict[str, Any], CapabilityIdentity, CapabilityIdentity] | None:
    source = candidate.source_fragment
    target = candidate.target_fragment
    if not _valid_fragment(source, trace_id) or not _valid_fragment(target, trace_id):
        return None
    if (
        source.trace_id != target.trace_id
        or source.continuity_index != target.continuity_index
        or _fragment_occurrence_id(source) == _fragment_occurrence_id(target)
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

    allowed_span_ids = frozenset(source.span_ids) | frozenset(target.span_ids)
    candidate_refs = _validated_evidence_refs(candidate.evidence_refs, trace_id, allowed_span_ids)
    decision_refs = _validated_evidence_refs(decision.evidence_refs, trace_id, allowed_span_ids)
    if candidate_refs is None or decision_refs is None:
        return None
    if len(decision_refs) < 2 or not set(decision_refs).issubset(candidate_refs):
        return None
    decision_span_ids = {_evidence_span_id(ref) for ref in decision_refs}
    if source.anchor_span_id not in decision_span_ids or target.anchor_span_id not in decision_span_ids:
        return None
    failure_reason = _nonempty_text(decision.reason)
    if decision.status == "failure" and failure_reason is None:
        return None

    source_identity = identity_index.resolve(source)
    target_identity = identity_index.resolve(target)
    if source_identity is None or target_identity is None:
        return None
    port_mapping = _unique_port_mapping(source_identity, target_identity)
    if port_mapping is None:
        return None

    metadata: dict[str, Any] = {
        "success": decision.status == "success",
        "evidence_refs": list(decision_refs),
        "evidence_method": decision.evidence_method,
        "evidence_strength": decision.evidence_strength,
        "candidate_id": candidate.candidate_id,
        "source_fragment_id": source.fragment_id,
        "target_fragment_id": target.fragment_id,
        "port_mappings": [port_mapping],
    }
    if decision.status == "failure":
        metadata["reason"] = failure_reason
    edge = {
        "source": source_identity.capability_id,
        "target": target_identity.capability_id,
        "relation": "can_feed",
        "metadata": metadata,
    }
    return edge, source_identity, target_identity


def _valid_fragment(fragment: Any, trace_id: str) -> bool:
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
        and fragment.trace_id == trace_id
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
        and _valid_identity_text(identity.version)
        and _valid_identity_text(identity.content_hash)
        and _valid_ports(identity.input_ports)
        and _valid_ports(identity.output_ports)
    )


def _valid_ports(ports: Any) -> bool:
    return (
        isinstance(ports, tuple) and all(_valid_identity_text(port) for port in ports) and len(set(ports)) == len(ports)
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


def _unique_port_mapping(
    source: CapabilityIdentity,
    target: CapabilityIdentity,
) -> dict[str, str] | None:
    if not _valid_ports(source.output_ports) or not _valid_ports(target.input_ports):
        return None
    if len(source.output_ports) != 1 or len(target.input_ports) != 1:
        return None
    return {
        "source_output": source.output_ports[0],
        "target_input": target.input_ports[0],
    }


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


def _evidence_span_id(ref: str) -> str:
    match = _EVIDENCE_REF_RE.fullmatch(ref)
    return match.group("span") if match is not None else ""


def _validated_evidence_refs(
    refs: Any,
    trace_id: str,
    allowed_span_ids: frozenset[str],
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
        if _validated_trace_id(matched_trace_id) is None or matched_trace_id != trace_id:
            return None
        if match.group("span") not in allowed_span_ids:
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

    graph = envelope.get("graph")
    nodes, edges = _validate_graph_shell(graph, "execution_graph")
    _validate_graph_nodes(nodes, execution=True)
    candidate_ids: set[str] = set()
    occurrence_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        metadata = _validate_graph_edge(edge, nodes)
        _validate_execution_edge_metadata(metadata, trace_id)
        candidate_id = metadata["candidate_id"]
        occurrence_pair = (metadata["source_fragment_id"], metadata["target_fragment_id"])
        if candidate_id in candidate_ids or occurrence_pair in occurrence_pairs:
            raise ValueError("duplicate execution edge identity")
        candidate_ids.add(candidate_id)
        occurrence_pairs.add(occurrence_pair)
        _validate_port_mapping_endpoints(edge, metadata, nodes)

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
        metadata = node.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("execution node metadata is required")
        if metadata.get("capability_type") != node.get("label"):
            raise ValueError("execution node capability_type must match its label")
        if _nonempty_text(metadata.get("version")) is None or _nonempty_text(metadata.get("content_hash")) is None:
            raise ValueError("execution node version and content_hash are required")
        for port_field in ("input_ports", "output_ports"):
            ports = metadata.get(port_field)
            if (
                not isinstance(ports, list)
                or any(not isinstance(port, str) or not port or port != port.strip() for port in ports)
                or len(set(ports)) != len(ports)
            ):
                raise ValueError(f"execution node {port_field} are invalid")


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


def _validate_execution_edge_metadata(metadata: Mapping[str, Any], trace_id: str) -> None:
    success = metadata.get("success")
    if not isinstance(success, bool):
        raise ValueError("execution edge success must be boolean")
    refs = metadata.get("evidence_refs")
    if not isinstance(refs, list) or len(set(refs)) < 2:
        raise ValueError("execution edge requires two distinct evidence refs")
    for ref in refs:
        if not isinstance(ref, str):
            raise ValueError("execution edge evidence refs must be strings")
        match = _EVIDENCE_REF_RE.fullmatch(ref)
        if match is None or match.group("trace") != trace_id or _validated_trace_id(match.group("trace")) is None:
            raise ValueError("execution edge evidence ref is invalid")
    method = metadata.get("evidence_method")
    strength = metadata.get("evidence_strength")
    if (method, strength) not in _METHOD_STRENGTH:
        raise ValueError("execution edge evidence method and strength are invalid")
    for field_name in ("candidate_id", "source_fragment_id", "target_fragment_id"):
        if _nonempty_text(metadata.get(field_name)) is None:
            raise ValueError(f"execution edge {field_name} is required")
    port_mappings = metadata.get("port_mappings")
    if not isinstance(port_mappings, list) or not port_mappings:
        raise ValueError("execution edge port_mappings are required")
    for mapping in port_mappings:
        if not isinstance(mapping, Mapping) or set(mapping) != {"source_output", "target_input"}:
            raise ValueError("execution edge port mapping is invalid")
        if _nonempty_text(mapping.get("source_output")) is None:
            raise ValueError("execution edge port mapping is invalid")
        if _nonempty_text(mapping.get("target_input")) is None:
            raise ValueError("execution edge port mapping is invalid")
    if success:
        if "reason" in metadata:
            raise ValueError("successful execution edge must omit reason")
    elif _nonempty_text(metadata.get("reason")) is None:
        raise ValueError("failed execution edge requires reason")


def _validate_port_mapping_endpoints(
    edge: Mapping[str, Any],
    metadata: Mapping[str, Any],
    nodes: Mapping[str, Any],
) -> None:
    source_metadata = nodes[edge["source"]]["metadata"]
    target_metadata = nodes[edge["target"]]["metadata"]
    mapping = metadata["port_mappings"][0]
    if len(metadata["port_mappings"]) != 1:
        raise ValueError("execution edge port mapping is not declared by its endpoints")
    if len(source_metadata["output_ports"]) != 1 or len(target_metadata["input_ports"]) != 1:
        raise ValueError("execution edge port mapping is not declared by its endpoints")
    if mapping["source_output"] not in source_metadata["output_ports"]:
        raise ValueError("execution edge port mapping is not declared by its endpoints")
    if mapping["target_input"] not in target_metadata["input_ports"]:
        raise ValueError("execution edge port mapping is not declared by its endpoints")


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
    "SymphonyGraphEvolutionSubmission",
    "SymphonyGraphObservationSink",
    "build_symphony_execution_graph",
    "build_symphony_graph_evolution_submission",
]
