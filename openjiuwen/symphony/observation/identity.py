"""Stable edge identity and current static-graph validation helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from openjiuwen.symphony.observation.contracts import CapabilityEvidence, EvolutionGraphEdge, PortMapping


def stable_hash(value: Any) -> str:
    """Hash one JSON-compatible value with deterministic key ordering."""

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capability_key(capability_type: str, capability_id: str) -> str:
    return f"{str(capability_type).strip().lower()}:{normalize_capability_id(capability_id)}"


def normalize_capability_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("skill:").removeprefix("capability:")


def normalize_port_mappings(values: Iterable[PortMapping | Mapping[str, Any]]) -> tuple[dict[str, str], ...]:
    mappings: set[tuple[tuple[str, str], ...]] = set()
    for value in values:
        raw = value.model_dump(exclude_none=True) if isinstance(value, PortMapping) else dict(value)
        normalized = {
            key: str(raw.get(key) or "").strip()
            for key in ("source_output", "target_input")
            if str(raw.get(key) or "").strip()
        }
        if not normalized.get("source_output") or not normalized.get("target_input"):
            continue
        mappings.add(tuple(sorted(normalized.items())))
    return tuple(dict(items) for items in sorted(mappings))


def static_edge_port_mappings(edge: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    evidence = edge.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    supporting = evidence.get("supporting_fields")
    supporting = supporting if isinstance(supporting, Mapping) else {}
    values = supporting.get("port_mappings") or evidence.get("port_mappings") or []
    return normalize_port_mappings(item for item in values if isinstance(item, Mapping))


@dataclass(frozen=True)
class EdgeIdentity:
    """A transition identity bound to endpoint content and port mapping."""

    source_id: str
    target_id: str
    relation_type: str
    source_content_hash: str
    target_content_hash: str
    port_mappings: tuple[dict[str, str], ...]

    @property
    def port_mapping_hash(self) -> str:
        return stable_hash(self.port_mappings)

    @property
    def identity_hash(self) -> str:
        return stable_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "source_content_hash": self.source_content_hash,
            "target_content_hash": self.target_content_hash,
            "port_mappings": list(self.port_mappings),
        }


@dataclass(frozen=True)
class StaticGraphIndex:
    """Identity and schema indexes derived from one immutable static artifact."""

    revision: str
    capability_ids: frozenset[str]
    graph_hash_by_id: Mapping[str, str]
    content_hash_by_id: Mapping[str, str]
    inputs_by_id: Mapping[str, frozenset[str]]
    outputs_by_id: Mapping[str, frozenset[str]]
    edges_by_identity: Mapping[str, Mapping[str, Any]]
    edge_identities: Mapping[str, EdgeIdentity]

    def validates_capabilities(self, values: Mapping[str, CapabilityEvidence]) -> bool:
        for capability_id, item in values.items():
            if capability_id not in self.capability_ids:
                return False
            if self.content_hash_by_id.get(capability_id) != item.content_hash:
                return False
        return True

    def validates_mapping(self, edge: EdgeIdentity) -> bool:
        if edge.source_id not in self.capability_ids or edge.target_id not in self.capability_ids:
            return False
        if self.content_hash_by_id.get(edge.source_id) != edge.source_content_hash:
            return False
        if self.content_hash_by_id.get(edge.target_id) != edge.target_content_hash:
            return False
        if not edge.port_mappings:
            return False
        source_outputs = self.outputs_by_id.get(edge.source_id, frozenset())
        target_inputs = self.inputs_by_id.get(edge.target_id, frozenset())
        return all(
            mapping["source_output"] in source_outputs and mapping["target_input"] in target_inputs
            for mapping in edge.port_mappings
        )


def edge_identity_from_observation(
    edge: EvolutionGraphEdge,
    capabilities: Mapping[str, CapabilityEvidence],
    static_index: StaticGraphIndex | None = None,
) -> EdgeIdentity | None:
    source_id = normalize_capability_id(edge.source_id)
    target_id = normalize_capability_id(edge.target_id)
    source = capabilities.get(source_id)
    target = capabilities.get(target_id)
    if source is None or target is None:
        return None
    identity = EdgeIdentity(
        source_id=source_id,
        target_id=target_id,
        relation_type=edge.relation_type,
        source_content_hash=source.content_hash,
        target_content_hash=target.content_hash,
        port_mappings=normalize_port_mappings(edge.metadata.port_mappings),
    )
    if identity.port_mappings:
        return identity
    mapping_hash = str(edge.metadata.port_mapping_hash or "").removeprefix("sha256:")
    if not mapping_hash or static_index is None:
        return None
    match: EdgeIdentity | None = None
    for candidate in static_index.edge_identities.values():
        if candidate.source_id != source_id:
            continue
        if candidate.target_id != target_id:
            continue
        if candidate.relation_type != edge.relation_type:
            continue
        if candidate.source_content_hash != source.content_hash:
            continue
        if candidate.target_content_hash != target.content_hash:
            continue
        if candidate.port_mapping_hash != mapping_hash:
            continue
        if match is not None:
            return None
        match = candidate
    return match


def build_static_graph_index(revision: str, payload: Mapping[str, Any]) -> StaticGraphIndex:
    capabilities = [item for item in payload.get("capabilities") or [] if isinstance(item, Mapping)]
    graph_hashes = payload.get("graph_identity_hashes")
    graph_hashes = graph_hashes if isinstance(graph_hashes, Mapping) else {}
    content_hashes = payload.get("capability_hashes")
    content_hashes = content_hashes if isinstance(content_hashes, Mapping) else {}

    graph_hash_by_id: dict[str, str] = {}
    content_hash_by_id: dict[str, str] = {}
    inputs_by_id: dict[str, frozenset[str]] = {}
    outputs_by_id: dict[str, frozenset[str]] = {}
    for capability in capabilities:
        capability_id = normalize_capability_id(capability.get("capability_id") or capability.get("id"))
        capability_type = str(capability.get("capability_type") or capability.get("type") or "skill")
        capability_identity_key = capability_key(capability_type, capability_id)
        graph_hash_by_id[capability_id] = str(graph_hashes.get(capability_identity_key) or "")
        content_hash_by_id[capability_id] = str(content_hashes.get(capability_identity_key) or "")
        inputs_by_id[capability_id] = _port_names(capability.get("inputs"))
        outputs_by_id[capability_id] = _port_names(capability.get("outputs"))

    edges_by_identity: dict[str, Mapping[str, Any]] = {}
    edge_identities: dict[str, EdgeIdentity] = {}
    for edge in payload.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        source_id = normalize_capability_id(edge.get("source"))
        target_id = normalize_capability_id(edge.get("target"))
        port_mappings = static_edge_port_mappings(edge)
        mapping_variants = [port_mappings]
        if len(port_mappings) > 1:
            mapping_variants.extend((mapping,) for mapping in port_mappings)
        for mapping_variant in mapping_variants:
            identity = EdgeIdentity(
                source_id=source_id,
                target_id=target_id,
                relation_type=str(edge.get("type") or "can_feed"),
                source_content_hash=content_hash_by_id.get(source_id, ""),
                target_content_hash=content_hash_by_id.get(target_id, ""),
                port_mappings=mapping_variant,
            )
            edges_by_identity[identity.identity_hash] = edge
            edge_identities[identity.identity_hash] = identity

    return StaticGraphIndex(
        revision=revision,
        capability_ids=frozenset(graph_hash_by_id),
        graph_hash_by_id=graph_hash_by_id,
        content_hash_by_id=content_hash_by_id,
        inputs_by_id=inputs_by_id,
        outputs_by_id=outputs_by_id,
        edges_by_identity=edges_by_identity,
        edge_identities=edge_identities,
    )


def _port_names(values: Any) -> frozenset[str]:
    return frozenset(
        str(item.get("name") or "").strip()
        for item in values or []
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    )
