"""Stable edge identity and current static-graph validation helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from openjiuwen.symphony.observation.contracts import EvolutionGraphEdge

POINT_EDGE_IDENTITY_SCHEMA = "symphony.point-edge.v1"


def stable_hash(value: Any) -> str:
    """Hash one JSON-compatible value with deterministic key ordering."""

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capability_key(capability_type: str, capability_id: str) -> str:
    return f"{str(capability_type).strip().lower()}:{normalize_capability_id(capability_id)}"


def normalize_capability_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("skill:").removeprefix("capability:")


def normalize_port_mappings(values: Iterable[Mapping[str, Any]]) -> tuple[dict[str, str], ...]:
    mappings: set[tuple[tuple[str, str], ...]] = set()
    for value in values:
        raw = dict(value)
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
    """A point-edge transition bound to endpoint content at one snapshot."""

    source_id: str
    target_id: str
    relation_type: str
    source_content_hash: str
    target_content_hash: str

    @property
    def identity_hash(self) -> str:
        return stable_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_schema": POINT_EDGE_IDENTITY_SCHEMA,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "source_content_hash": self.source_content_hash,
            "target_content_hash": self.target_content_hash,
        }


@dataclass(frozen=True)
class StaticGraphIndex:
    """Identity and schema indexes derived from one immutable static artifact."""

    revision: str
    capability_ids: frozenset[str]
    graph_hash_by_id: Mapping[str, str]
    content_hash_by_id: Mapping[str, str]
    edges_by_identity: Mapping[str, Mapping[str, Any]]
    edge_identities: Mapping[str, EdgeIdentity]

    def validates_nodes(self, node_ids: Iterable[str]) -> bool:
        return all(normalize_capability_id(value) in self.capability_ids for value in node_ids)


def edge_identity_from_observation(
    edge: EvolutionGraphEdge,
    static_index: StaticGraphIndex,
) -> EdgeIdentity | None:
    source_id = normalize_capability_id(edge.source_id)
    target_id = normalize_capability_id(edge.target_id)
    source_content_hash = static_index.content_hash_by_id.get(source_id)
    target_content_hash = static_index.content_hash_by_id.get(target_id)
    if not source_content_hash or not target_content_hash:
        return None
    return EdgeIdentity(
        source_id=source_id,
        target_id=target_id,
        relation_type=edge.relation_type,
        source_content_hash=source_content_hash,
        target_content_hash=target_content_hash,
    )


def build_static_graph_index(revision: str, payload: Mapping[str, Any]) -> StaticGraphIndex:
    capabilities = [item for item in payload.get("capabilities") or [] if isinstance(item, Mapping)]
    graph_hashes = payload.get("graph_identity_hashes")
    graph_hashes = graph_hashes if isinstance(graph_hashes, Mapping) else {}
    content_hashes = payload.get("capability_hashes")
    content_hashes = content_hashes if isinstance(content_hashes, Mapping) else {}

    graph_hash_by_id: dict[str, str] = {}
    content_hash_by_id: dict[str, str] = {}
    for capability in capabilities:
        capability_id = normalize_capability_id(capability.get("capability_id") or capability.get("id"))
        capability_type = str(capability.get("capability_type") or capability.get("type") or "skill")
        capability_identity_key = capability_key(capability_type, capability_id)
        graph_hash_by_id[capability_id] = str(graph_hashes.get(capability_identity_key) or "")
        content_hash_by_id[capability_id] = str(content_hashes.get(capability_identity_key) or "")

    edges_by_identity: dict[str, Mapping[str, Any]] = {}
    edge_identities: dict[str, EdgeIdentity] = {}
    for edge in payload.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        source_id = normalize_capability_id(edge.get("source"))
        target_id = normalize_capability_id(edge.get("target"))
        identity = EdgeIdentity(
            source_id=source_id,
            target_id=target_id,
            relation_type=str(edge.get("type") or "can_feed"),
            source_content_hash=content_hash_by_id.get(source_id, ""),
            target_content_hash=content_hash_by_id.get(target_id, ""),
        )
        edges_by_identity[identity.identity_hash] = edge
        edge_identities[identity.identity_hash] = identity

    return StaticGraphIndex(
        revision=revision,
        capability_ids=frozenset(graph_hash_by_id),
        graph_hash_by_id=graph_hash_by_id,
        content_hash_by_id=content_hash_by_id,
        edges_by_identity=edges_by_identity,
        edge_identities=edge_identities,
    )
