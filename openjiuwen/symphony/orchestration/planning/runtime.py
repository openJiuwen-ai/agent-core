"""Shared runtime-edge resolution for all Symphony graph planners."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from openjiuwen.symphony.orchestration.artifacts import GraphArtifacts
from openjiuwen.symphony.observation.identity import normalize_port_mappings, static_edge_port_mappings
from openjiuwen.symphony.orchestration.planning.utils import CAN_FEED, eligible_can_feed_edges, skill_id


class RuntimeEdgeResolver:
    """Merge qualified observation evidence into one query-scoped edge view."""

    def __init__(
        self,
        artifacts: GraphArtifacts,
        *,
        min_edge_confidence: float,
        candidate_skill_ids: Sequence[str] | None,
        dynamic_overlay: Mapping[str, Any] | None,
        task_cluster_id: str | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.min_edge_confidence = min_edge_confidence
        self.candidate_skill_ids = frozenset(candidate_skill_ids or ())
        self.dynamic_overlay = dynamic_overlay if isinstance(dynamic_overlay, Mapping) else {}
        self.task_cluster_id = str(task_cluster_id or "").strip()
        self.summary: dict[str, Any] = {
            "available": bool(self._overlay_edges()),
            "applied_edges": 0,
            "runtime_only_edges": 0,
        }

    def resolve(self) -> list[dict[str, Any]]:
        """Return sorted static and qualified runtime-only edges."""

        static_edges = eligible_can_feed_edges(
            self.artifacts.graph.get("edges", []),
            known_skill_ids=set(self.artifacts.skill_by_id),
            min_confidence=self.min_edge_confidence,
        )
        static_stats_by_relation: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
        runtime_only_by_identity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for stats in self._overlay_edges().values():
            if not isinstance(stats, Mapping) or not self._is_relevant(stats):
                continue
            key = _relation_key(stats)
            status = str(stats.get("binding_status") or "")
            if status == "active_static" or (not status and not bool(stats.get("runtime_only"))):
                static_stats_by_relation[key].append(stats)
            elif status == "active_runtime_only" or (not status and _legacy_runtime_only_eligible(stats)):
                identity = str(stats.get("edge_identity") or "") or repr(
                    (key, tuple(sorted(_mapping_keys(stats.get("port_mappings") or ()))))
                )
                runtime_only_by_identity[identity].append(stats)

        static_edges_by_relation: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for edge in static_edges:
            static_edges_by_relation[_relation_key(edge)].append(edge)

        resolved = []
        applied_static = 0
        for edge in static_edges:
            stats = [
                item
                for item in static_stats_by_relation.get(_relation_key(edge), ())
                if _stats_match_static_edge(item, edge)
            ]
            if not stats:
                resolved.append(dict(edge))
                continue
            runtime = _combine_stats(stats)
            confidence = float(edge.get("confidence") or 0.0)
            resolved.append(
                {
                    **edge,
                    "runtime_weight": runtime["runtime_weight"],
                    "effective_weight": confidence * runtime["runtime_weight"],
                    "runtime_evidence": runtime,
                }
            )
            applied_static += 1

        runtime_only_count = 0
        for stats in runtime_only_by_identity.values():
            key = _relation_key(stats[0])
            if (
                any(_stats_match_static_edge(stats[0], edge) for edge in static_edges_by_relation.get(key, ()))
                or key[2] != CAN_FEED
            ):
                continue
            source_id, target_id, _relation_type = key
            if source_id not in self.artifacts.skill_by_id or target_id not in self.artifacts.skill_by_id:
                continue
            runtime = _combine_stats(stats)
            attempt_count = max(runtime["attempt_count"], runtime["success_count"])
            confidence = (runtime["success_count"] + 1) / (attempt_count + 2)
            port_mappings = _combined_port_mappings(stats)
            resolved.append(
                {
                    "type": CAN_FEED,
                    "source": source_id,
                    "target": target_id,
                    "confidence": confidence,
                    "method": "runtime_observed",
                    "evidence": {
                        "reasons": ["Repeated strongly verified execution evidence"],
                        "supporting_fields": {"port_mappings": port_mappings},
                    },
                    "runtime_only": True,
                    "runtime_weight": runtime["runtime_weight"],
                    "effective_weight": confidence * runtime["runtime_weight"],
                    "runtime_evidence": runtime,
                }
            )
            runtime_only_count += 1

        self.summary = {
            "available": bool(self._overlay_edges()),
            "applied_edges": applied_static + runtime_only_count,
            "runtime_only_edges": runtime_only_count,
        }
        return sorted(
            resolved,
            key=lambda item: (
                -edge_weight(item),
                str(item.get("source") or ""),
                str(item.get("target") or ""),
            ),
        )

    def _is_relevant(self, stats: Mapping[str, Any]) -> bool:
        clusters = {str(value) for value in stats.get("task_cluster_ids") or [] if str(value)}
        cluster_matches = bool(self.task_cluster_id and self.task_cluster_id in clusters)
        endpoints = {
            skill_id(stats.get("source_id") or stats.get("source")),
            skill_id(stats.get("target_id") or stats.get("target")),
        }
        touches_retrieval_seed = bool(self.candidate_skill_ids & endpoints)
        return cluster_matches or touches_retrieval_seed

    def _overlay_edges(self) -> Mapping[str, Any]:
        edges = self.dynamic_overlay.get("edges")
        return edges if isinstance(edges, Mapping) else {}


def edge_weight(edge: Mapping[str, Any]) -> float:
    """Return the planner ordering weight for one resolved edge."""

    return float(edge.get("effective_weight") or edge.get("confidence") or 0.0)


def _relation_key(value: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        skill_id(value.get("source_id") or value.get("source")),
        skill_id(value.get("target_id") or value.get("target")),
        str(value.get("relation_type") or value.get("type") or CAN_FEED),
    )


def _legacy_runtime_only_eligible(stats: Mapping[str, Any]) -> bool:
    return (
        int(stats.get("success_count") or 0) >= 2
        and int(stats.get("failure_count") or 0) == 0
        and bool(stats.get("runtime_only", True))
    )


def _stats_match_static_edge(stats: Mapping[str, Any], edge: Mapping[str, Any]) -> bool:
    if _relation_key(stats) != _relation_key(edge):
        return False
    observed = _mapping_keys(stats.get("port_mappings") or ())
    static = _mapping_keys(static_edge_port_mappings(edge))
    return bool(observed) and observed <= static


def _mapping_keys(values: Any) -> set[tuple[str, str]]:
    return {
        (mapping["source_output"], mapping["target_input"])
        for mapping in normalize_port_mappings(item for item in values if isinstance(item, Mapping))
    }


def _combine_stats(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    success_count = sum(int(item.get("success_count") or 0) for item in values)
    failure_count = sum(int(item.get("failure_count") or 0) for item in values)
    attempt_count = sum(int(item.get("attempt_count") or 0) for item in values)
    weighted_attempts = sum(max(1, int(item.get("attempt_count") or 0)) for item in values)
    runtime_weight = sum(
        _bounded_runtime_weight(item) * max(1, int(item.get("attempt_count") or 0)) for item in values
    ) / max(1, weighted_attempts)
    return {
        "success_count": success_count,
        "failure_count": failure_count,
        "attempt_count": attempt_count,
        "distinct_success_sessions": len(
            {session for item in values for session in item.get("success_session_hashes") or []}
        ),
        "runtime_weight": runtime_weight,
    }


def _bounded_runtime_weight(stats: Mapping[str, Any]) -> float:
    try:
        if "runtime_adjustment" in stats:
            value = 1.0 + float(stats["runtime_adjustment"])
        else:
            value = float(stats.get("runtime_weight") or 1.0)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.0, min(2.0, value))


def _combined_port_mappings(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    mappings: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
    for item in values:
        for mapping in item.get("port_mappings") or []:
            if not isinstance(mapping, Mapping):
                continue
            normalized = {str(key): str(value) for key, value in mapping.items() if str(value)}
            mappings[tuple(sorted(normalized.items()))] = normalized
    return [mappings[key] for key in sorted(mappings)]
