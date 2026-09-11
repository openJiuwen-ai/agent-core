# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Build bounded Symphony execution-edge candidates from trace evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Literal, cast
from unicodedata import category as unicode_category
from urllib.parse import SplitResult, urlsplit

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_tool_call,
    span_attributes,
    span_identity,
    span_sort_key,
)
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    SymphonyExecutionFragment,
)

EdgeStatus = Literal["success", "failure", "no_relation", "insufficient_evidence"]
EvidenceMethod = Literal["deterministic", "model_assisted"]
EvidenceStrength = Literal["strong", "low", "none"]

_STRUCTURED_REASON = "structured_reference"
_SUBAGENT_DISPATCH_REASON = "subagent_dispatch"
_SUBAGENT_RESULT_REASON = "subagent_result"
_PLANNED_REASON = "planned"
_OBSERVED_ORDER_REASON = "observed_order"
_PROXIMITY_REASON_PREFIX = "proximity:"
_SKILL_TOOL_NAME = "skill_tool"
_DISPATCH_TOOL_NAMES = frozenset({"task_tool", "subagent_spawn", "sessions_spawn"})
_DISPATCH_CORRELATION_KINDS = frozenset({"task", "tool_call"})
_HTTP_URI_SCHEMES = frozenset({"http", "https"})
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_ARTIFACT_ID_KEYS = frozenset({"artifactid", "artifactref"})
_PATH_KEYS = frozenset(
    {
        "path",
        "filepath",
        "relativefilepath",
        "absolutefilepath",
        "inputpath",
        "outputpath",
        "sourcepath",
        "targetpath",
        "artifactpath",
    }
)
_URI_KEYS = frozenset({"uri", "url", "resourceuri", "artifacturi"})
_TASK_ID_KEYS = frozenset({"taskid", "parenttaskid", "origintaskid"})
_MESSAGE_ID_KEYS = frozenset({"messageid", "parentmessageid"})
_TOOL_CALL_ID_KEYS = frozenset({"toolcallid", "callid"})
_REASON_ORDER = {
    _STRUCTURED_REASON: 0,
    _SUBAGENT_DISPATCH_REASON: 1,
    _SUBAGENT_RESULT_REASON: 2,
    _PLANNED_REASON: 3,
    _OBSERVED_ORDER_REASON: 4,
}


@dataclass(frozen=True)
class SymphonyEdgeCandidate:
    """One occurrence-specific directed pair for bounded model evaluation."""

    candidate_id: str
    source_fragment: SymphonyExecutionFragment
    target_fragment: SymphonyExecutionFragment
    evidence_refs: tuple[str, ...]
    candidate_reasons: tuple[str, ...]


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


@dataclass(frozen=True, order=True)
class _StructuredReference:
    kind: str
    value: str


@dataclass
class _CandidateParts:
    source: SymphonyExecutionFragment
    target: SymphonyExecutionFragment
    reasons: set[str]
    evidence_refs: set[str]


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


@dataclass(frozen=True)
class _FragmentReferenceIndex:
    produced: Mapping[str, Mapping[_StructuredReference, set[str]]]
    consumed: Mapping[str, Mapping[_StructuredReference, set[str]]]
    subagent_results: Mapping[str, Mapping[_StructuredReference, set[str]]]


@dataclass(frozen=True)
class _ConsumerBucket:
    """Target fragments indexed by canonical order and maximum consume time."""

    fragment_ids: tuple[str, ...]
    max_starts: tuple[int, ...]
    tree_size: int
    max_tree: tuple[int, ...]

    def first_matching(self, threshold: int, limit: int | None) -> tuple[str, ...]:
        matches: list[str] = []

        def visit(node: int, left: int, right: int) -> None:
            if (limit is not None and len(matches) >= limit) or self.max_tree[node] < threshold:
                return
            if right - left == 1:
                if left < len(self.fragment_ids):
                    matches.append(self.fragment_ids[left])
                return
            middle = (left + right) // 2
            visit(node * 2, left, middle)
            visit(node * 2 + 1, middle, right)

        visit(1, 0, self.tree_size)
        return tuple(matches)


def build_symphony_edge_candidates(
    fragments: Sequence[SymphonyExecutionFragment],
    continuities: Sequence[tuple[int, Trajectory]],
    *,
    planned_graph: Mapping[str, Any] | None = None,
    edge_search_max_depth: int = 3,
    max_candidates: int | None = 64,
    include_team_member_pairs: bool = False,
) -> tuple[SymphonyEdgeCandidate, ...]:
    """Build exact-reference, planned, observed-order and proximity candidates.

    Planned and observed ordering only bound the model search space.  They do
    not create a resolved edge decision, and every evidence reference names a
    span present in the matching continuity and trace.
    """

    if max_candidates is not None and max_candidates <= 0:
        return ()

    span_index = _index_spans(continuities)
    ordered_fragments = _ordered_valid_fragments(fragments, span_index)
    reference_index = _index_fragment_references(ordered_fragments, span_index)
    parts: dict[tuple[str, str], _CandidateParts] = {}

    exact_full = _add_exact_candidates(
        parts,
        ordered_fragments,
        span_index,
        reference_index,
        max_candidates=max_candidates,
    )

    by_branch: dict[tuple[int, str, str], list[SymphonyExecutionFragment]] = defaultdict(list)
    for fragment in ordered_fragments:
        by_branch[(fragment.continuity_index, fragment.trace_id, fragment.branch_span_id)].append(fragment)

    if exact_full:
        return _enrich_and_finalize_candidates(
            parts,
            by_branch,
            planned_graph,
            span_index,
            edge_search_max_depth=max(0, edge_search_max_depth),
            include_team_member_pairs=include_team_member_pairs,
        )

    if planned_graph is None:
        if _add_observed_order_candidates(
            parts,
            by_branch,
            span_index,
            capability_types=frozenset({"skill", "subagent"} if include_team_member_pairs else {"skill"}),
            max_candidates=max_candidates,
        ):
            return _enrich_and_finalize_candidates(
                parts,
                by_branch,
                planned_graph,
                span_index,
                edge_search_max_depth=max(0, edge_search_max_depth),
                include_team_member_pairs=include_team_member_pairs,
            )
    else:
        if _add_planned_candidates(
            parts,
            by_branch,
            planned_graph,
            span_index=span_index,
            max_candidates=max_candidates,
        ):
            return _enrich_and_finalize_candidates(
                parts,
                by_branch,
                planned_graph,
                span_index,
                edge_search_max_depth=max(0, edge_search_max_depth),
                include_team_member_pairs=include_team_member_pairs,
            )
        if _add_proximity_candidates(
            parts,
            by_branch,
            planned_graph,
            edge_search_max_depth=max(0, edge_search_max_depth),
            span_index=span_index,
            max_candidates=max_candidates,
        ):
            return _enrich_and_finalize_candidates(
                parts,
                by_branch,
                planned_graph,
                span_index,
                edge_search_max_depth=max(0, edge_search_max_depth),
                include_team_member_pairs=include_team_member_pairs,
            )
    if include_team_member_pairs:
        _add_observed_order_candidates(
            parts,
            by_branch,
            span_index,
            capability_types=frozenset({"subagent"}),
            max_candidates=max_candidates,
        )
    return _enrich_and_finalize_candidates(
        parts,
        by_branch,
        planned_graph,
        span_index,
        edge_search_max_depth=max(0, edge_search_max_depth),
        include_team_member_pairs=include_team_member_pairs,
    )


def _enrich_and_finalize_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    planned_graph: Mapping[str, Any] | None,
    span_index: _SpanIndex,
    *,
    edge_search_max_depth: int,
    include_team_member_pairs: bool,
) -> tuple[SymphonyEdgeCandidate, ...]:
    """Complete selected candidates before applying their canonical order."""

    _enrich_selected_candidates(
        parts,
        by_branch,
        planned_graph,
        span_index,
        edge_search_max_depth=edge_search_max_depth,
        include_team_member_pairs=include_team_member_pairs,
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
                    sorted(
                        item.reasons,
                        key=lambda reason: (_REASON_ORDER.get(reason, 99), reason),
                    )
                ),
            )
        )
    candidates.sort(
        key=lambda candidate: (
            _candidate_priority(candidate),
            _candidate_proximity_depth(candidate),
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


def _add_exact_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    fragments: Sequence[SymphonyExecutionFragment],
    span_index: _SpanIndex,
    reference_index: _FragmentReferenceIndex,
    *,
    max_candidates: int | None,
) -> bool:
    """Add exact candidates through an inverted reference index.

    This avoids probing every fragment pair when references are sparse.  A
    finite budget stops the first priority tier as soon as it is full, so a
    common reference cannot expand into an unbounded Cartesian product.
    """

    fragment_by_id = {fragment.fragment_id: fragment for fragment in fragments}
    fragment_order = {fragment.fragment_id: index for index, fragment in enumerate(fragments)}
    consumers = _build_consumer_buckets(fragments, span_index, reference_index, fragment_order)
    sources_by_anchor: dict[tuple[int, str, str], list[SymphonyExecutionFragment]] = defaultdict(list)
    for source in fragments:
        sources_by_anchor[(source.continuity_index, source.trace_id, source.anchor_span_id)].append(source)
    topology_targets: dict[str, list[str]] = defaultdict(list)
    for target in fragments:
        if target.capability_type != "subagent":
            continue
        dispatch_span = span_index.anchor_for(target)
        if (
            dispatch_span is None
            or _canonical_tool_name(read_tool_call(dispatch_span).get("name")) not in _DISPATCH_TOOL_NAMES
        ):
            continue
        current = target.anchor_span_id
        visited: set[str] = set()
        while current not in visited:
            visited.add(current)
            span = span_index.span_for(target, current)
            if span is None:
                break
            parent = str(span.get("parentSpanId") or "").strip()
            if not parent:
                break
            for source in sources_by_anchor.get((target.continuity_index, target.trace_id, parent), ()):
                topology_targets[source.fragment_id].append(target.fragment_id)
            current = parent

    for source in fragments:
        remaining = None if max_candidates is None else max_candidates - len(parts)
        if remaining is not None and remaining <= 0:
            return True
        produced_groups = _source_reference_groups(source, reference_index)
        target_ids: set[str] = set(topology_targets.get(source.fragment_id, ())[:remaining])
        for _, produced in produced_groups:
            for reference in sorted(produced):
                threshold = _minimum_producer_end(source, produced[reference], span_index)
                bucket = consumers.get((source.continuity_index, source.trace_id, reference))
                if threshold is None or bucket is None:
                    continue
                query_limit = None if remaining is None else remaining + 1
                target_ids.update(bucket.first_matching(threshold, query_limit))
        selected_ids = sorted(
            (target_id for target_id in target_ids if target_id != source.fragment_id),
            key=fragment_order.__getitem__,
        )
        if remaining is not None:
            selected_ids = selected_ids[:remaining]
        for target_id in selected_ids:
            target = fragment_by_id[target_id]
            if source.fragment_id == target.fragment_id:
                continue
            item = _candidate_parts(parts, source, target)
            for default_reason, produced in produced_groups:
                consumed = reference_index.consumed.get(target.fragment_id, {})
                for reference in sorted(produced.keys() & consumed.keys()):
                    pairs = _matching_reference_maps(
                        source,
                        target,
                        span_index,
                        {reference: produced[reference]},
                        {reference: consumed[reference]},
                        require_order=True,
                    )
                    if not pairs:
                        continue
                    reason = default_reason
                    if (
                        target.capability_type == "subagent"
                        and reference.kind in _DISPATCH_CORRELATION_KINDS
                        and target.anchor_span_id in consumed[reference]
                    ):
                        reason = _SUBAGENT_DISPATCH_REASON
                    item.reasons.add(reason)
                    item.evidence_refs.update(_evidence_refs_for_pairs(source.trace_id, pairs))
            if target_id in topology_targets.get(source.fragment_id, ()):
                item.reasons.add(_SUBAGENT_DISPATCH_REASON)
                item.evidence_refs.update(
                    {
                        _evidence_ref(source.trace_id, source.anchor_span_id),
                        _evidence_ref(target.trace_id, target.anchor_span_id),
                    }
                )
        if max_candidates is not None and len(parts) >= max_candidates:
            return True
    return False


def _source_reference_groups(
    source: SymphonyExecutionFragment,
    reference_index: _FragmentReferenceIndex,
) -> list[tuple[str, Mapping[_StructuredReference, set[str]]]]:
    groups: list[tuple[str, Mapping[_StructuredReference, set[str]]]] = [
        (_STRUCTURED_REASON, reference_index.produced.get(source.fragment_id, {})),
    ]
    if source.capability_type == "subagent":
        groups.insert(
            0,
            (_SUBAGENT_RESULT_REASON, reference_index.subagent_results.get(source.fragment_id, {})),
        )
    return groups


def _minimum_producer_end(
    source: SymphonyExecutionFragment,
    span_ids: set[str],
    span_index: _SpanIndex,
) -> int | None:
    ends: list[int] = []
    for span_id in span_ids:
        span = span_index.span_for(source, span_id)
        if span is None:
            continue
        interval = _span_interval(span)
        if interval is not None:
            ends.append(interval[1])
    return min(ends, default=None)


def _build_consumer_buckets(
    fragments: Sequence[SymphonyExecutionFragment],
    span_index: _SpanIndex,
    reference_index: _FragmentReferenceIndex,
    fragment_order: Mapping[str, int],
) -> dict[tuple[int, str, _StructuredReference], _ConsumerBucket]:
    raw: dict[tuple[int, str, _StructuredReference], dict[str, int]] = defaultdict(dict)
    for target in fragments:
        for reference, span_ids in reference_index.consumed.get(target.fragment_id, {}).items():
            starts: list[int] = []
            for span_id in span_ids:
                span = span_index.span_for(target, span_id)
                if span is None:
                    continue
                interval = _span_interval(span)
                if interval is not None:
                    starts.append(interval[0])
            if starts:
                raw[(target.continuity_index, target.trace_id, reference)][target.fragment_id] = max(starts)
    buckets: dict[tuple[int, str, _StructuredReference], _ConsumerBucket] = {}
    for key, targets in raw.items():
        ordered = sorted(targets.items(), key=lambda item: fragment_order[item[0]])
        fragment_ids = tuple(item[0] for item in ordered)
        max_starts = tuple(item[1] for item in ordered)
        tree_size = 1
        while tree_size < len(max_starts):
            tree_size *= 2
        tree = [-1] * (tree_size * 2)
        tree_end = tree_size + len(max_starts)
        tree[tree_size:tree_end] = max_starts
        for node in range(tree_size - 1, 0, -1):
            tree[node] = max(tree[node * 2], tree[node * 2 + 1])
        buckets[key] = _ConsumerBucket(fragment_ids, max_starts, tree_size, tuple(tree))
    return buckets


def _add_candidate(
    parts: dict[tuple[str, str], _CandidateParts],
    source: SymphonyExecutionFragment,
    target: SymphonyExecutionFragment,
    *,
    reason: str,
    evidence_refs: set[str] | None = None,
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
    return max_candidates is not None and len(parts) >= max_candidates


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
    if not fragment.capability_name or fragment.capability_name not in _node_aliases(node_id, node):
        return False
    metadata = node.get("metadata") if isinstance(node, Mapping) else None
    planned_type = str(metadata.get("type") or "").strip() if isinstance(metadata, Mapping) else ""
    return not planned_type or planned_type == fragment.capability_type


def _add_planned_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    planned_graph: object,
    *,
    span_index: _SpanIndex,
    max_candidates: int | None,
) -> bool:
    for source, target in _iter_planned_pairs(by_branch, planned_graph, span_index):
        if _add_candidate(
            parts,
            source,
            target,
            reason=_PLANNED_REASON,
            max_candidates=max_candidates,
        ):
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
    ordered_specs: list[tuple[object, object]] = []
    for key in sorted(specs):
        spec = specs.get(key)
        if spec is not None:
            ordered_specs.append(spec)
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
            target = None
            source_position = positions.get(source.fragment_id)
            for item in branch:
                if item.fragment_id in used_targets[used_key]:
                    continue
                item_position = positions.get(item.fragment_id)
                if source_position is None or item_position is None:
                    continue
                if item_position <= source_position or item.capability_name == source.capability_name:
                    continue
                if not _matches_planned_node(item, target_id, untyped_nodes.get(target_id)):
                    continue
                target = item
                break
            if target is not None:
                used_targets[used_key].add(target.fragment_id)
                selected.add(target.fragment_id)
        targets = sorted(
            (item for item in branch if item.fragment_id in selected),
            key=lambda fragment: _fragment_sort_key(fragment, span_index),
        )
        for target in targets:
            yield source, target


def _enrich_selected_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    planned_graph: Mapping[str, Any] | None,
    span_index: _SpanIndex,
    *,
    edge_search_max_depth: int,
    include_team_member_pairs: bool,
) -> None:
    """Complete lower-priority reasons without materializing more candidates."""

    if planned_graph is not None:
        for source, target in _iter_planned_pairs(by_branch, planned_graph, span_index):
            item = parts.get((source.fragment_id, target.fragment_id))
            if item is not None:
                item.reasons.add(_PLANNED_REASON)

    nodes, _ = _planned_parts(planned_graph)
    skills_by_branch = {
        branch_key: [fragment for fragment in branch if fragment.capability_type == "skill"]
        for branch_key, branch in by_branch.items()
    }
    skill_positions: dict[str, tuple[tuple[int, str, str], int]] = {}
    planned_ids: set[str] = set()
    for branch_key, skills in skills_by_branch.items():
        for index, fragment in enumerate(skills):
            skill_positions[fragment.fragment_id] = (branch_key, index)
            if any(_matches_planned_node(fragment, node_id, node) for node_id, node in nodes.items()):
                planned_ids.add(fragment.fragment_id)
    for item in parts.values():
        source = item.source
        target = item.target
        same_name = source.capability_name == target.capability_name
        source_position = skill_positions.get(source.fragment_id)
        target_position = skill_positions.get(target.fragment_id)
        if planned_graph is not None and not same_name:
            if source_position is not None and target_position is not None:
                source_branch, source_index = source_position
                target_branch, target_index = target_position
                depth = target_index - source_index
                if source_branch == target_branch and 0 < depth <= edge_search_max_depth:
                    if not nodes or source.fragment_id not in planned_ids:
                        item.reasons.add(f"{_PROXIMITY_REASON_PREFIX}{source.fragment_id}:after:{depth}")
                    if not nodes or target.fragment_id not in planned_ids:
                        item.reasons.add(f"{_PROXIMITY_REASON_PREFIX}{target.fragment_id}:before:{depth}")

        observed_types = {"skill"} if planned_graph is None else set()
        if include_team_member_pairs:
            observed_types.add("subagent")
        if (
            same_name
            or source.capability_type not in observed_types
            or source.capability_type != target.capability_type
        ):
            continue
        source_branch = (source.continuity_index, source.trace_id, source.branch_span_id)
        target_branch = (target.continuity_index, target.trace_id, target.branch_span_id)
        if source_branch != target_branch:
            continue
        branch = by_branch.get(source_branch, ())
        positions = {fragment.fragment_id: index for index, fragment in enumerate(branch)}
        if positions.get(source.fragment_id, -1) < positions.get(target.fragment_id, -1):
            item.reasons.add(_OBSERVED_ORDER_REASON)


def _add_proximity_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    planned_graph: object,
    *,
    edge_search_max_depth: int,
    span_index: _SpanIndex,
    max_candidates: int | None,
) -> bool:
    if edge_search_max_depth <= 0:
        return False
    nodes, _ = _planned_parts(planned_graph)
    skills_by_branch = {
        branch_key: [fragment for fragment in branch if fragment.capability_type == "skill"]
        for branch_key, branch in by_branch.items()
    }
    branch_for = {
        fragment.fragment_id: branch_key for branch_key, skills in skills_by_branch.items() for fragment in skills
    }
    positions = {
        fragment.fragment_id: index for skills in skills_by_branch.values() for index, fragment in enumerate(skills)
    }
    ordered_sources = sorted(
        (fragment for skills in skills_by_branch.values() for fragment in skills),
        key=lambda fragment: _fragment_sort_key(fragment, span_index),
    )
    planned_ids = {
        fragment.fragment_id
        for fragment in ordered_sources
        if any(_matches_planned_node(fragment, node_id, node) for node_id, node in nodes.items())
    }
    for depth in range(1, edge_search_max_depth + 1):
        for source in ordered_sources:
            skills = skills_by_branch[branch_for[source.fragment_id]]
            target_position = positions[source.fragment_id] + depth
            if target_position >= len(skills):
                continue
            target = skills[target_position]
            if source.capability_name == target.capability_name:
                continue
            deviations: list[tuple[SymphonyExecutionFragment, str]] = []
            if not nodes or source.fragment_id not in planned_ids:
                deviations.append((source, "after"))
            if not nodes or target.fragment_id not in planned_ids:
                deviations.append((target, "before"))
            if not deviations:
                continue
            for deviation, direction in deviations:
                item = _candidate_parts(parts, source, target)
                item.reasons.add(f"{_PROXIMITY_REASON_PREFIX}{deviation.fragment_id}:{direction}:{depth}")
            if max_candidates is not None and len(parts) >= max_candidates:
                return True
    return False


def _add_observed_order_candidates(
    parts: dict[tuple[str, str], _CandidateParts],
    by_branch: Mapping[tuple[int, str, str], Sequence[SymphonyExecutionFragment]],
    span_index: _SpanIndex,
    *,
    capability_types: frozenset[str],
    max_candidates: int | None,
) -> bool:
    eligible_by_branch = {
        key: [item for item in branch if item.capability_type in capability_types] for key, branch in by_branch.items()
    }
    branch_for = {fragment.fragment_id: key for key, branch in eligible_by_branch.items() for fragment in branch}
    positions = {
        fragment.fragment_id: index for branch in eligible_by_branch.values() for index, fragment in enumerate(branch)
    }
    ordered_sources = sorted(
        (fragment for branch in eligible_by_branch.values() for fragment in branch),
        key=lambda fragment: _fragment_sort_key(fragment, span_index),
    )
    for source in ordered_sources:
        branch = eligible_by_branch[branch_for[source.fragment_id]]
        target_start = positions[source.fragment_id] + 1
        for target in branch[target_start:]:
            if source.capability_type != target.capability_type or source.capability_name == target.capability_name:
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


def _candidate_priority(candidate: SymphonyEdgeCandidate) -> int:
    reasons = set(candidate.candidate_reasons)
    if reasons & {_STRUCTURED_REASON, _SUBAGENT_DISPATCH_REASON, _SUBAGENT_RESULT_REASON}:
        return 0
    if _PLANNED_REASON in reasons:
        return 1
    if any(reason.startswith(_PROXIMITY_REASON_PREFIX) for reason in reasons):
        return 2
    return 3


def _candidate_proximity_depth(candidate: SymphonyEdgeCandidate) -> int:
    if _candidate_priority(candidate) != 2:
        return 0
    depths: list[int] = []
    for reason in candidate.candidate_reasons:
        if reason.startswith(_PROXIMITY_REASON_PREFIX):
            try:
                depths.append(int(reason.rsplit(":", 1)[-1]))
            except ValueError:
                pass
    return min(depths, default=0)


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
    return sorted(
        unique.values(),
        key=lambda fragment: (
            fragment.continuity_index,
            fragment.trace_id,
            span_sort_key(span_index.anchor_for(fragment) or {}),
            fragment.fragment_id,
        ),
    )


def _index_spans(continuities: Sequence[tuple[int, Trajectory]]) -> _SpanIndex:
    spans: dict[tuple[int, str, str], Mapping[str, Any]] = {}
    for continuity_index, trajectory in sorted(continuities, key=lambda item: item[0]):
        for span in iter_spans(trajectory):
            identity = span_identity(span)
            if identity is not None:
                spans.setdefault((int(continuity_index), identity[0], identity[1]), span)
    return _SpanIndex(spans)


def _index_fragment_references(
    fragments: Sequence[SymphonyExecutionFragment],
    span_index: _SpanIndex,
) -> _FragmentReferenceIndex:
    unique = {fragment.fragment_id: fragment for fragment in fragments}
    return _FragmentReferenceIndex(
        produced={key: _fragment_references(item, span_index, mode="produced") for key, item in unique.items()},
        consumed={key: _fragment_references(item, span_index, mode="consumed") for key, item in unique.items()},
        subagent_results={
            key: _subagent_result_references(item, span_index)
            for key, item in unique.items()
            if item.capability_type == "subagent"
        },
    )


def _matching_reference_maps(
    source: SymphonyExecutionFragment,
    target: SymphonyExecutionFragment,
    span_index: _SpanIndex,
    produced: Mapping[_StructuredReference, set[str]],
    consumed: Mapping[_StructuredReference, set[str]],
    *,
    require_order: bool = False,
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for reference in produced.keys() & consumed.keys():
        for source_span_id in produced[reference]:
            source_span = span_index.span_for(source, source_span_id)
            for target_span_id in consumed[reference]:
                target_span = span_index.span_for(target, target_span_id)
                if source_span is None or target_span is None:
                    continue
                if require_order and not _producer_precedes_consumer(source_span, target_span):
                    continue
                pairs.add((source_span_id, target_span_id))
    return pairs


def _producer_precedes_consumer(producer: Mapping[str, Any], consumer: Mapping[str, Any]) -> bool:
    producer_interval = _span_interval(producer)
    consumer_interval = _span_interval(consumer)
    if producer_interval is None or consumer_interval is None:
        return False
    return producer_interval[1] <= consumer_interval[0]


def _span_interval(span: Mapping[str, Any]) -> tuple[int, int] | None:
    start = _timestamp(span.get("startTimeUnixNano"))
    end = _timestamp(span.get("endTimeUnixNano"))
    if start is None or end is None:
        return None
    if start < 0 or end < start:
        return None
    return start, end


def _timestamp(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    return None


def _subagent_result_references(
    fragment: SymphonyExecutionFragment,
    span_index: _SpanIndex,
) -> dict[_StructuredReference, set[str]]:
    references: dict[_StructuredReference, set[str]] = defaultdict(set)
    anchor = span_index.anchor_for(fragment)
    if anchor is not None:
        for reference in _structured_references(read_tool_call(anchor).get("output")):
            references[reference].add(fragment.anchor_span_id)
    branch = span_index.span_for(fragment, fragment.branch_span_id)
    if branch is not None and fragment.branch_span_id in fragment.span_ids:
        for reference in _structured_references(span_attributes(branch).get(semconv.AT_AGENT_OUTPUT)):
            references[reference].add(fragment.branch_span_id)
    return references


def _evidence_refs_for_pairs(trace_id: str, pairs: set[tuple[str, str]]) -> set[str]:
    return {_evidence_ref(trace_id, span_id) for pair in pairs for span_id in pair}


def _fragment_references(
    fragment: SymphonyExecutionFragment,
    span_index: _SpanIndex,
    *,
    mode: Literal["produced", "consumed"],
) -> dict[_StructuredReference, set[str]]:
    references: dict[_StructuredReference, set[str]] = defaultdict(set)
    for span_id in fragment.span_ids:
        span = span_index.span_for(fragment, span_id)
        if span is None:
            continue
        tool_call = read_tool_call(span)
        if _canonical_tool_name(tool_call.get("name")) == _SKILL_TOOL_NAME:
            continue
        payload = tool_call.get("output") if mode == "produced" else tool_call.get("input")
        for reference in _structured_references(payload):
            references[reference].add(span_id)
    return references


def _structured_references(value: Any) -> set[_StructuredReference]:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                value = json.loads(stripped)
            except (TypeError, ValueError):
                return set()
    references: set[_StructuredReference] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            kind = _reference_kind(str(key))
            if kind is not None:
                references.update(_references_for_value(kind, item))
            if isinstance(item, (Mapping, list, tuple)):
                references.update(_structured_references(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            references.update(_structured_references(item))
    return references


def _reference_kind(key: str) -> str | None:
    token = re.sub(r"[^a-z0-9]", "", key.lower())
    if token in _ARTIFACT_ID_KEYS or token.endswith("artifactid"):
        return "artifact"
    if token in _PATH_KEYS or token.endswith("filepath"):
        return "path"
    if token in _URI_KEYS or token.endswith("uri"):
        return "uri"
    if token in _TASK_ID_KEYS or token.endswith("taskid"):
        return "task"
    if token in _MESSAGE_ID_KEYS or token.endswith("messageid"):
        return "message"
    if token in _TOOL_CALL_ID_KEYS or token.endswith("toolcallid"):
        return "tool_call"
    return None


def _references_for_value(kind: str, value: Any) -> set[_StructuredReference]:
    if isinstance(value, (list, tuple)):
        return {reference for item in value for reference in _references_for_value(kind, item)}
    if isinstance(value, Mapping):
        return set()
    normalized = (
        _normalize_path(value) if kind == "path" else _normalize_uri(value) if kind == "uri" else _normalize_id(value)
    )
    return {_StructuredReference(kind, normalized)} if normalized is not None else set()


def _normalize_id(value: Any) -> str | None:
    if value is None or isinstance(value, (bool, Mapping, list, tuple)):
        return None
    text = str(value)
    return text if 0 < len(text) <= 512 and _is_safe_reference_text(text) else None


def _normalize_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if not value or len(value) > 2048 or not _is_safe_reference_text(value):
        return None
    if "://" in value or not value.startswith("/") or "//" in value:
        return None
    if "\\" in value or any(segment in {".", ".."} for segment in value.split("/")):
        return None
    return value


def _normalize_uri(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if not _is_safe_reference_text(value):
        return None
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return None
    if not parsed.scheme or re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return None
    scheme = parsed.scheme.lower()
    if scheme in _HTTP_URI_SCHEMES:
        if not _valid_http_authority(parsed.netloc, parsed.hostname, parsed):
            return None
        authority_start = value.find(":") + 3
        authority_end = authority_start + len(parsed.netloc)
        return f"{scheme}://{_normalize_http_netloc(parsed.netloc)}{value[authority_end:]}"
    opaque = value.split(":", 1)[1]
    return value if not parsed.netloc and opaque and not opaque.startswith("/") else None


def _is_safe_reference_text(text: str) -> bool:
    return text == text.strip() and all(unicode_category(character) != "Cc" for character in text)


def _normalize_http_netloc(netloc: str) -> str:
    userinfo, separator, host_port = netloc.rpartition("@")
    prefix = f"{userinfo}@" if separator else ""
    if host_port.startswith("["):
        closing = host_port.find("]")
        suffix_start = closing + 1
        return f"{prefix}[{host_port[1:closing].lower()}]{host_port[suffix_start:]}"
    host, separator, port = host_port.rpartition(":")
    return f"{prefix}{host.lower()}:{port}" if separator else f"{prefix}{host_port.lower()}"


def _valid_http_authority(netloc: str, hostname: str | None, parsed: SplitResult) -> bool:
    if not netloc or not hostname:
        return False
    if "%" in hostname or "\\" in netloc:
        return False
    try:
        parsed.port
    except ValueError:
        return False
    if ":" in hostname:
        try:
            return ip_address(hostname).version == 6
        except ValueError:
            return False
    labels = hostname.rstrip(".").split(".")
    return len(hostname) <= 253 and bool(labels) and all(_HOST_LABEL_RE.fullmatch(label) for label in labels)


def _canonical_tool_name(value: Any) -> str:
    return str(value or "").strip().lower().rsplit(".", 1)[-1].replace("-", "_")


def _evidence_ref(trace_id: str, span_id: str) -> str:
    return f"{trace_id}#span={span_id}"


__all__ = [
    "SymphonyEdgeCandidate",
    "SymphonyEdgeDecision",
    "build_model_edge_decisions",
    "build_symphony_edge_candidates",
]
