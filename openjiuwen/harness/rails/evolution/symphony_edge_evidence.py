# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Build bounded Symphony Skill-edge candidates from trace evidence."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    span_identity,
    span_sort_key,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    SymphonyExecutionFragment,
)

EdgeStatus = Literal["success", "failure", "no_relation", "insufficient_evidence"]
EvidenceMethod = Literal["deterministic", "model_assisted"]
EvidenceStrength = Literal["strong", "low", "none"]

_PLANNED_REASON = "planned"
_OBSERVED_ORDER_REASON = "observed_order"
_INTERRUPT_CONTINUATION_REASON = "interrupt_continuation"
_REASON_ORDER = {
    _INTERRUPT_CONTINUATION_REASON: 0,
    _PLANNED_REASON: 1,
    _OBSERVED_ORDER_REASON: 2,
}


@dataclass(frozen=True)
class SymphonyInterruptContinuation:
    """One exact, adjacent interrupt-resume boundary."""

    source_trace_id: str
    target_trace_id: str
    continuity_index: int
    source_segment_index: int
    target_segment_index: int
    trace_ids: tuple[str, ...]


@dataclass(frozen=True)
class SymphonyEdgeCandidate:
    """One occurrence-specific directed pair for bounded model evaluation."""

    candidate_id: str
    source_fragment: SymphonyExecutionFragment
    target_fragment: SymphonyExecutionFragment
    evidence_refs: tuple[str, ...]
    candidate_reasons: tuple[str, ...]
    interrupt_continuation: SymphonyInterruptContinuation | None = None


@dataclass(frozen=True)
class SymphonyEdgeDecision:
    """A fail-closed or model-produced decision for one candidate."""

    candidate_id: str
    source_fragment_id: str
    target_fragment_id: str
    status: EdgeStatus
    reason: str
    evidence_refs: tuple[str, ...]
    evidence_method: EvidenceMethod
    evidence_strength: EvidenceStrength


@dataclass
class _CandidateParts:
    source: SymphonyExecutionFragment
    target: SymphonyExecutionFragment
    reasons: set[str]
    evidence_refs: set[str]
    interrupt_continuation: SymphonyInterruptContinuation | None = None


@dataclass(frozen=True)
class _SpanIndex:
    spans: Mapping[tuple[int, str, str], Mapping[str, Any]]

    def span_for(
        self,
        fragment: SymphonyExecutionFragment,
        span_id: str,
    ) -> Mapping[str, Any] | None:
        return self.spans.get((fragment.continuity_index, fragment.trace_id, span_id))

    def anchor_for(self, fragment: SymphonyExecutionFragment) -> Mapping[str, Any] | None:
        return self.span_for(fragment, fragment.anchor_span_id)


def build_symphony_edge_candidates(
    fragments: Sequence[SymphonyExecutionFragment],
    continuities: Sequence[tuple[int, Trajectory]],
    *,
    planned_graph: Mapping[str, Any] | None = None,
    edge_search_max_depth: int = 3,
    max_candidates: int | None = 64,
    interrupt_continuations: Sequence[SymphonyInterruptContinuation] = (),
) -> tuple[SymphonyEdgeCandidate, ...]:
    """Build planned or observed-order Skill candidates.

    A ready planned graph is the exclusive candidate prior. Without one,
    observed Skill order bounds the model search space by
    ``edge_search_max_depth``. Neither path creates a resolved edge decision.
    """

    if max_candidates is not None and max_candidates <= 0:
        return ()

    span_index = _index_spans(continuities)
    ordered_fragments = [
        fragment for fragment in _ordered_valid_fragments(fragments, span_index) if fragment.capability_type == "skill"
    ]
    parts: dict[tuple[str, str], _CandidateParts] = {}
    by_branch: dict[tuple[int, str, str], list[SymphonyExecutionFragment]] = defaultdict(list)
    for fragment in ordered_fragments:
        by_branch[(fragment.continuity_index, fragment.trace_id, fragment.branch_span_id)].append(fragment)

    if _is_ready_directed_plan(planned_graph):
        _add_interrupt_continuation_candidates(
            parts,
            ordered_fragments,
            planned_graph,
            span_index,
            interrupt_continuations,
            max_candidates=max_candidates,
        )
        if max_candidates is None or len(parts) < max_candidates:
            _add_planned_candidates(
                parts,
                by_branch,
                planned_graph,
                span_index=span_index,
                max_candidates=max_candidates,
            )
    else:
        _add_observed_order_candidates(
            parts,
            by_branch,
            span_index,
            edge_search_max_depth=max(0, edge_search_max_depth),
            max_candidates=max_candidates,
        )
    return _finalize_candidates(parts, span_index)


def _finalize_candidates(
    parts: Mapping[tuple[str, str], _CandidateParts],
    span_index: _SpanIndex,
) -> tuple[SymphonyEdgeCandidate, ...]:
    candidates: list[SymphonyEdgeCandidate] = []
    for item in parts.values():
        _add_anchor_refs(item, span_index)
        candidates.append(
            SymphonyEdgeCandidate(
                candidate_id=_candidate_id(item.source, item.target),
                source_fragment=item.source,
                target_fragment=item.target,
                evidence_refs=tuple(sorted(item.evidence_refs)),
                candidate_reasons=tuple(
                    sorted(item.reasons, key=lambda reason: (_REASON_ORDER.get(reason, 99), reason))
                ),
                interrupt_continuation=item.interrupt_continuation,
            )
        )
    candidates.sort(
        key=lambda candidate: (
            _REASON_ORDER.get(candidate.candidate_reasons[0], 99),
            _candidate_sort_key(candidate, span_index),
        )
    )
    return tuple(candidates)


def build_model_edge_decisions(
    candidates: Sequence[SymphonyEdgeCandidate],
) -> tuple[SymphonyEdgeDecision, ...]:
    """Return fail-closed initial decisions; only the evaluator may resolve them."""

    return tuple(
        SymphonyEdgeDecision(
            candidate_id=candidate.candidate_id,
            source_fragment_id=candidate.source_fragment.fragment_id,
            target_fragment_id=candidate.target_fragment.fragment_id,
            status="insufficient_evidence",
            reason="awaiting_model_evidence",
            evidence_refs=(),
            evidence_method="deterministic",
            evidence_strength="none",
        )
        for candidate in candidates
    )


def _add_candidate(
    parts: dict[tuple[str, str], _CandidateParts],
    source: SymphonyExecutionFragment,
    target: SymphonyExecutionFragment,
    *,
    reason: str,
    evidence_refs: set[str] | None = None,
    interrupt_continuation: SymphonyInterruptContinuation | None = None,
    max_candidates: int | None,
) -> bool:
    key = (source.fragment_id, target.fragment_id)
    if source.fragment_id == target.fragment_id:
        return False
    if key not in parts and max_candidates is not None and len(parts) >= max_candidates:
        return True
    item = _candidate_parts(parts, source, target)
    item.reasons.add(reason)
    item.evidence_refs.update(evidence_refs or ())
    if interrupt_continuation is not None:
        item.interrupt_continuation = interrupt_continuation
    return max_candidates is not None and len(parts) >= max_candidates


def _add_interrupt_continuation_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    fragments: Sequence[SymphonyExecutionFragment],
    planned_graph: Mapping[str, Any] | None,
    span_index: _SpanIndex,
    continuations: Sequence[SymphonyInterruptContinuation],
    *,
    max_candidates: int | None,
) -> None:
    """Add only exact, planned Skill edges across an interactive resume."""

    if not _is_ready_directed_plan(planned_graph):
        return
    nodes, graph = _planned_parts(planned_graph)
    edges = graph.get("edges") if graph else None
    if not isinstance(edges, Sequence) or isinstance(edges, (str, bytes)):
        return
    valid_continuations = _valid_interrupt_continuations(continuations)
    if not valid_continuations:
        return
    by_segment: dict[tuple[int, str], list[SymphonyExecutionFragment]] = defaultdict(list)
    for fragment in fragments:
        if fragment.capability_type == "skill":
            by_segment[(fragment.continuity_index, fragment.trace_id)].append(fragment)
    for segment in by_segment.values():
        segment.sort(key=lambda fragment: _fragment_sort_key(fragment, span_index))

    for continuation in valid_continuations:
        if max_candidates is not None and len(parts) >= max_candidates:
            return
        source_items = by_segment.get((continuation.continuity_index, continuation.source_trace_id), ())
        target_items = by_segment.get((continuation.continuity_index, continuation.target_trace_id), ())
        if not source_items or not target_items:
            continue
        for edge in edges:
            if not isinstance(edge, Mapping):
                continue
            source_id, target_id = edge.get("source"), edge.get("target")
            try:
                valid_edge = source_id in nodes and target_id in nodes
            except TypeError:
                valid_edge = False
            if not valid_edge:
                continue
            source_matches = [
                fragment for fragment in source_items if _matches_planned_node(fragment, source_id, nodes[source_id])
            ]
            target_matches = [
                fragment for fragment in target_items if _matches_planned_node(fragment, target_id, nodes[target_id])
            ]
            if (
                not source_matches
                or not target_matches
                or len({item.branch_span_id for item in source_matches}) != 1
                or len({item.branch_span_id for item in target_matches}) != 1
            ):
                continue
            source = source_matches[-1]
            target = target_matches[0]
            if _add_candidate(
                parts,
                source,
                target,
                reason=_INTERRUPT_CONTINUATION_REASON,
                evidence_refs={
                    _evidence_ref(source.trace_id, source.anchor_span_id),
                    _evidence_ref(target.trace_id, target.anchor_span_id),
                },
                interrupt_continuation=continuation,
                max_candidates=max_candidates,
            ):
                return


def _valid_interrupt_continuations(
    continuations: Sequence[SymphonyInterruptContinuation],
) -> tuple[SymphonyInterruptContinuation, ...]:
    try:
        items = tuple(continuations)
    except MemoryError:
        raise
    except Exception:
        return ()
    valid = [
        item
        for item in items
        if isinstance(item, SymphonyInterruptContinuation)
        and isinstance(item.source_trace_id, str)
        and bool(item.source_trace_id.strip())
        and isinstance(item.target_trace_id, str)
        and bool(item.target_trace_id.strip())
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
        and all(isinstance(trace_id, str) and trace_id.strip() for trace_id in item.trace_ids)
        and item.trace_ids[item.source_segment_index] == item.source_trace_id
        and item.trace_ids[item.target_segment_index] == item.target_trace_id
    ]
    by_boundary: dict[tuple[int, str, str], set[SymphonyInterruptContinuation]] = defaultdict(set)
    for item in valid:
        by_boundary[(item.continuity_index, item.source_trace_id, item.target_trace_id)].add(item)
    return tuple(next(iter(values)) for key, values in sorted(by_boundary.items()) if len(values) == 1)


def _is_ready_directed_plan(planned_graph: Mapping[str, Any] | None) -> bool:
    nodes, graph = _planned_parts(planned_graph)
    metadata = graph.get("metadata") if graph else None
    return (
        bool(nodes)
        and graph.get("type") == "planned_graph"
        and graph.get("directed") is True
        and isinstance(metadata, Mapping)
        and metadata.get("status") == "ready"
    )


def _add_anchor_refs(item: _CandidateParts, span_index: _SpanIndex) -> None:
    for fragment in (item.source, item.target):
        if span_index.anchor_for(fragment) is not None:
            item.evidence_refs.add(_evidence_ref(fragment.trace_id, fragment.anchor_span_id))


def _planned_parts(planned_graph: object) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(planned_graph, Mapping):
        return {}, {}
    graph = planned_graph.get("graph")
    if not isinstance(graph, Mapping):
        return {}, {}
    nodes = graph.get("nodes")
    return (nodes if isinstance(nodes, Mapping) else {}), graph


def _node_aliases(node_id: object, node: object) -> set[str]:
    aliases = {str(node_id).strip()}
    if isinstance(node, Mapping):
        aliases.add(str(node.get("label") or "").strip())
        metadata = node.get("metadata")
        if isinstance(metadata, Mapping):
            aliases.update(str(metadata.get(key) or "").strip() for key in ("name", "capability_name"))
    return {alias for alias in aliases if alias}


def _matches_planned_node(
    fragment: SymphonyExecutionFragment,
    node_id: object,
    node: object,
) -> bool:
    if fragment.capability_type != "skill":
        return False
    if not fragment.capability_name or fragment.capability_name not in _node_aliases(node_id, node):
        return False
    metadata = node.get("metadata") if isinstance(node, Mapping) else None
    planned_type = str(metadata.get("type") or "").strip() if isinstance(metadata, Mapping) else ""
    return not planned_type or planned_type == "skill"


def _add_planned_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    planned_graph: object,
    *,
    span_index: _SpanIndex,
    max_candidates: int | None,
) -> bool:
    for source, target in _iter_planned_pairs(by_branch, planned_graph, span_index):
        if _add_candidate(parts, source, target, reason=_PLANNED_REASON, max_candidates=max_candidates):
            return True
    return False


def _iter_planned_pairs(
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    planned_graph: object,
    span_index: _SpanIndex,
) -> Iterator[tuple[SymphonyExecutionFragment, SymphonyExecutionFragment]]:
    nodes, graph = _planned_parts(planned_graph)
    edges = graph.get("edges") if graph else None
    if not isinstance(edges, Sequence) or isinstance(edges, (str, bytes)):
        return
    specs: dict[tuple[str, str], tuple[object, object]] = {}
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        source_id, target_id = edge.get("source"), edge.get("target")
        try:
            valid = source_id in nodes and target_id in nodes
        except TypeError:
            valid = False
        if valid:
            specs.setdefault((str(source_id), str(target_id)), (source_id, target_id))
    ordered_specs = [specs[key] for key in sorted(specs)]
    branch_for = {
        fragment.fragment_id: branch_key
        for branch_key, branch_fragments in by_branch.items()
        for fragment in branch_fragments
    }
    positions = {
        fragment.fragment_id: index
        for branch_fragments in by_branch.values()
        for index, fragment in enumerate(branch_fragments)
    }
    used_targets: dict[tuple[tuple[int, str, str], str, str], set[str]] = defaultdict(set)
    ordered_sources = sorted(
        (fragment for branch in by_branch.values() for fragment in branch),
        key=lambda fragment: _fragment_sort_key(fragment, span_index),
    )
    for source in ordered_sources:
        branch_key = branch_for[source.fragment_id]
        branch = by_branch[branch_key]
        selected: set[str] = set()
        for source_id, target_id in ordered_specs:
            untyped_nodes = cast(Mapping[Any, Any], nodes)
            if not _matches_planned_node(source, source_id, untyped_nodes.get(source_id)):
                continue
            used_key = (branch_key, str(source_id), str(target_id))
            source_position = positions.get(source.fragment_id)
            target = None
            for item in branch:
                if item.fragment_id in used_targets[used_key]:
                    continue
                item_position = positions.get(item.fragment_id)
                if source_position is None or item_position is None or item_position <= source_position:
                    continue
                if item.capability_name == source.capability_name:
                    continue
                if _matches_planned_node(item, target_id, untyped_nodes.get(target_id)):
                    target = item
                    break
            if target is not None:
                used_targets[used_key].add(target.fragment_id)
                selected.add(target.fragment_id)
        for target in sorted(
            (item for item in branch if item.fragment_id in selected),
            key=lambda fragment: _fragment_sort_key(fragment, span_index),
        ):
            yield source, target


def _add_observed_order_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    span_index: _SpanIndex,
    *,
    edge_search_max_depth: int,
    max_candidates: int | None,
) -> bool:
    if edge_search_max_depth <= 0:
        return False
    ordered_sources = sorted(
        (fragment for branch in by_branch.values() for fragment in branch),
        key=lambda fragment: _fragment_sort_key(fragment, span_index),
    )
    branch_for = {fragment.fragment_id: branch_key for branch_key, branch in by_branch.items() for fragment in branch}
    positions = {fragment.fragment_id: index for branch in by_branch.values() for index, fragment in enumerate(branch)}
    for source in ordered_sources:
        branch = by_branch[branch_for[source.fragment_id]]
        source_position = positions[source.fragment_id]
        for target in branch[source_position + 1 : source_position + edge_search_max_depth + 1]:
            if source.capability_name == target.capability_name:
                continue
            if _add_candidate(
                parts,
                source,
                target,
                reason=_OBSERVED_ORDER_REASON,
                max_candidates=max_candidates,
            ):
                return True
    return False


def _candidate_parts(
    parts: dict[tuple[str, str], _CandidateParts],
    source: SymphonyExecutionFragment,
    target: SymphonyExecutionFragment,
) -> _CandidateParts:
    key = (source.fragment_id, target.fragment_id)
    return parts.setdefault(key, _CandidateParts(source, target, set(), set()))


def _candidate_id(source: SymphonyExecutionFragment, target: SymphonyExecutionFragment) -> str:
    occurrence = f"{source.fragment_id}\x00{target.fragment_id}".encode()
    return f"edge-{hashlib.sha256(occurrence).hexdigest()[:24]}"


def _candidate_sort_key(candidate: SymphonyEdgeCandidate, span_index: _SpanIndex) -> tuple[Any, ...]:
    return (
        *_fragment_sort_key(candidate.source_fragment, span_index),
        *_fragment_sort_key(candidate.target_fragment, span_index),
    )


def _fragment_sort_key(fragment: SymphonyExecutionFragment, span_index: _SpanIndex) -> tuple[Any, ...]:
    return (
        fragment.continuity_index,
        fragment.trace_id,
        span_sort_key(span_index.anchor_for(fragment) or {}),
        fragment.fragment_id,
    )


def _ordered_valid_fragments(
    fragments: Sequence[SymphonyExecutionFragment],
    span_index: _SpanIndex,
) -> list[SymphonyExecutionFragment]:
    unique: dict[str, SymphonyExecutionFragment] = {}
    for fragment in fragments:
        if span_index.anchor_for(fragment) is not None:
            unique.setdefault(fragment.fragment_id, fragment)
    return sorted(unique.values(), key=lambda fragment: _fragment_sort_key(fragment, span_index))


def _index_spans(continuities: Sequence[tuple[int, Trajectory]]) -> _SpanIndex:
    spans: dict[tuple[int, str, str], Mapping[str, Any]] = {}
    for continuity_index, trajectory in sorted(continuities, key=lambda item: item[0]):
        for span in iter_spans(trajectory):
            identity = span_identity(span)
            if identity is not None:
                spans.setdefault((int(continuity_index), identity[0], identity[1]), span)
    return _SpanIndex(spans)


def _evidence_ref(trace_id: str, span_id: str) -> str:
    return f"{trace_id}#span={span_id}"


__all__ = [
    "SymphonyEdgeCandidate",
    "SymphonyEdgeDecision",
    "SymphonyInterruptContinuation",
    "build_model_edge_decisions",
    "build_symphony_edge_candidates",
]
