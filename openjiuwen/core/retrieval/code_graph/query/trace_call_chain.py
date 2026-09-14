# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evidence-backed call chain traversal over resolved ``calls`` edges.

Only edges the indexer could resolve without guessing are walked. Call sites it
could not resolve are reported separately as ``unresolved_edges`` so a caller
can tell "no callers" apart from "callers exist but are undecidable".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphIndex,
    RelationKind,
    Symbol,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path

DIRECTION_CALLERS = "callers"
DIRECTION_CALLEES = "callees"
DIRECTION_BOTH = "both"
VALID_DIRECTIONS = (DIRECTION_CALLERS, DIRECTION_CALLEES, DIRECTION_BOTH)

CALLABLE_KINDS = frozenset(
    {SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS}
)

# Applied when an edge carries no evidence, e.g. an index built before evidence
# was recorded. Ranking must not treat such an edge as fully trusted.
UNKNOWN_EDGE_CONFIDENCE = 0.5
UNKNOWN_RESOLUTION = "unknown"


@dataclass(frozen=True)
class TraceLimits:
    """Hard bounds so one tool call cannot walk an entire monorepo."""

    max_depth: int = 3
    max_paths: int = 20
    max_nodes: int = 200
    time_budget_seconds: float = 5.0

    def normalized(self) -> "TraceLimits":
        return TraceLimits(
            max_depth=max(1, min(int(self.max_depth), 10)),
            max_paths=max(1, min(int(self.max_paths), 100)),
            max_nodes=max(1, min(int(self.max_nodes), 2000)),
            time_budget_seconds=max(0.1, float(self.time_budget_seconds)),
        )


def resolve_call_symbol(
    index: CodeGraphIndex,
    symbol_id: str,
) -> tuple[Symbol | None, list[Symbol]]:
    """Resolve to one callable symbol, or return the ambiguous candidates."""
    wanted = str(symbol_id or "").strip()
    if not wanted:
        return None, []
    exact = index.symbols.get(wanted)
    if exact is not None:
        return exact, []
    ids = index.by_name.get(wanted.lower(), [])
    candidates = [
        index.symbols[sid]
        for sid in ids
        if sid in index.symbols and index.symbols[sid].kind in CALLABLE_KINDS
    ]
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates


def trace_call_chain(
    index: CodeGraphIndex,
    symbol_id: str,
    *,
    direction: str = DIRECTION_BOTH,
    limits: TraceLimits | None = None,
    include_tests: bool = False,
) -> dict[str, object]:
    """Enumerate maximal call paths around ``symbol_id``.

    A path ends at a leaf or at ``max_depth``. Intermediate nodes are visible as
    prefixes of longer paths, which keeps the payload small enough to put in a
    prompt.
    """
    normalized_direction = str(direction or DIRECTION_BOTH).strip().lower()
    if normalized_direction not in VALID_DIRECTIONS:
        return status_payload(
            CodeGraphStatus.ERROR,
            message=f"direction must be one of {list(VALID_DIRECTIONS)}",
            extra={"symbol_id": symbol_id, "direction": direction},
        )
    start, candidates = resolve_call_symbol(index, symbol_id)
    if start is None:
        if candidates:
            return status_payload(
                CodeGraphStatus.AMBIGUOUS,
                message=(
                    f"{len(candidates)} symbols are named {symbol_id!r}; "
                    "retry with one of the listed symbol_id values"
                ),
                extra={
                    "symbol_id": symbol_id,
                    "candidates": [_node(item) for item in candidates],
                    "index_snapshot": index.snapshot,
                },
            )
        return status_payload(
            CodeGraphStatus.NO_MATCH,
            message=f"no callable symbol for {symbol_id!r}",
            extra={"symbol_id": symbol_id, "index_snapshot": index.snapshot},
        )

    bounds = (limits or TraceLimits()).normalized()
    deadline = time.monotonic() + bounds.time_budget_seconds
    directions = (
        (DIRECTION_CALLERS, DIRECTION_CALLEES)
        if normalized_direction == DIRECTION_BOTH
        else (normalized_direction,)
    )

    paths: list[dict[str, object]] = []
    visited: set[str] = {start.symbol_id}
    truncated = False
    warnings: list[str] = []
    for item in directions:
        walked, hit_limit = _walk(
            index,
            start,
            item,
            bounds=bounds,
            include_tests=include_tests,
            deadline=deadline,
            visited=visited,
        )
        paths.extend(walked)
        truncated = truncated or hit_limit

    paths.sort(key=_path_sort_key)
    if len(paths) > bounds.max_paths:
        paths = paths[: bounds.max_paths]
        truncated = True
    if truncated:
        warnings.append("result truncated by depth, path, node, or time limit")

    unresolved = _unresolved_for(index, visited, include_tests=include_tests)
    if unresolved and not paths:
        warnings.append(
            "no resolvable call edges; the listed call sites are ambiguous"
        )

    status = CodeGraphStatus.COMPLETE
    if not paths:
        status = CodeGraphStatus.PARTIAL if unresolved else CodeGraphStatus.NO_MATCH
    elif truncated:
        status = CodeGraphStatus.PARTIAL

    return status_payload(
        status,
        message=_message(status, paths, start),
        extra={
            "start": _node(start),
            "direction": normalized_direction,
            "paths": paths,
            "unresolved_edges": unresolved,
            "truncated": truncated,
            "node_count": len(visited),
            "index_snapshot": index.snapshot,
            "warnings": warnings,
        },
    )


def _walk(
    index: CodeGraphIndex,
    start: Symbol,
    direction: str,
    *,
    bounds: TraceLimits,
    include_tests: bool,
    deadline: float,
    visited: set[str],
) -> tuple[list[dict[str, object]], bool]:
    """Depth-first enumeration of maximal paths in one direction."""
    relation = (
        RelationKind.CALLED_BY if direction == DIRECTION_CALLERS else RelationKind.CALLS
    )
    paths: list[dict[str, object]] = []
    truncated = False
    # Each frame is a full path: nodes plus the edges that produced them.
    stack: list[tuple[list[Symbol], list[dict[str, object]]]] = [([start], [])]
    while stack:
        if time.monotonic() > deadline or len(visited) > bounds.max_nodes:
            truncated = True
            break
        nodes, edges = stack.pop()
        current = nodes[-1]
        extended = False
        if len(nodes) - 1 < bounds.max_depth:
            on_path = {item.symbol_id for item in nodes}
            for neighbor_id in index.neighbors(current.symbol_id, relation):
                neighbor = index.symbols.get(neighbor_id)
                if neighbor is None or neighbor_id in on_path:
                    continue
                if not include_tests and is_test_path(neighbor.file):
                    continue
                edge = _edge(index, current, neighbor, direction)
                visited.add(neighbor_id)
                stack.append(([*nodes, neighbor], [*edges, edge]))
                extended = True
        elif index.neighbors(current.symbol_id, relation):
            truncated = True
        if not extended and edges:
            paths.append(_path(direction, nodes, edges))
        if len(paths) > bounds.max_paths:
            truncated = True
            break
    return paths, truncated


def _edge(
    index: CodeGraphIndex,
    current: Symbol,
    neighbor: Symbol,
    direction: str,
) -> dict[str, object]:
    if direction == DIRECTION_CALLERS:
        source, target = neighbor.symbol_id, current.symbol_id
        relation = RelationKind.CALLED_BY.value
    else:
        source, target = current.symbol_id, neighbor.symbol_id
        relation = RelationKind.CALLS.value
    evidence = index.evidence_for(source, RelationKind.CALLS, target)
    best = max(evidence, key=lambda item: item.confidence) if evidence else None
    return {
        "source": source,
        "relation": relation,
        "target": target,
        "evidence": best.to_dict() if best is not None else None,
        "resolution": best.resolution if best is not None else UNKNOWN_RESOLUTION,
        "confidence": best.confidence if best is not None else UNKNOWN_EDGE_CONFIDENCE,
        "call_sites": len(evidence),
    }


def _path(
    direction: str,
    nodes: Sequence[Symbol],
    edges: Sequence[dict[str, object]],
) -> dict[str, object]:
    confidences = [float(edge.get("confidence") or 0.0) for edge in edges]
    return {
        "direction": direction,
        "depth": len(nodes) - 1,
        "confidence": min(confidences) if confidences else 0.0,
        "nodes": [_node(item) for item in nodes],
        "edges": list(edges),
    }


def _path_sort_key(path: dict[str, object]) -> tuple:
    nodes = path.get("nodes") or []
    tests = sum(
        1 for node in nodes if isinstance(node, dict) and is_test_path(str(node.get("file") or ""))
    )
    trail = "|".join(str(node.get("symbol_id")) for node in nodes if isinstance(node, dict))
    return (int(path.get("depth") or 0), -float(path.get("confidence") or 0.0), tests, trail)


def _unresolved_for(
    index: CodeGraphIndex,
    visited: set[str],
    *,
    include_tests: bool,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in index.unresolved_calls:
        if item.caller_id not in visited:
            continue
        if not include_tests and is_test_path(item.file):
            continue
        rows.append(item.to_dict())
    return rows


def _node(symbol: Symbol) -> dict[str, object]:
    return {
        "symbol_id": symbol.symbol_id,
        "name": symbol.name,
        "kind": symbol.kind.value,
        "file": symbol.file,
        "start_line": symbol.start_line,
        "end_line": symbol.end_line,
        "qualified_name": symbol.qualified_name or symbol.name,
    }


def _message(status: CodeGraphStatus, paths: list[dict[str, object]], start: Symbol) -> str:
    if status == CodeGraphStatus.NO_MATCH:
        return f"no call edges for {start.symbol_id}"
    if status == CodeGraphStatus.PARTIAL and not paths:
        return f"only unresolved call sites for {start.symbol_id}"
    suffix = " (truncated)" if status == CodeGraphStatus.PARTIAL else ""
    return f"traced {len(paths)} call paths for {start.symbol_id}{suffix}"
