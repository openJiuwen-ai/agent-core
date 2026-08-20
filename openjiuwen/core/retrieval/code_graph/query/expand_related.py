# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Expand graph neighbors from a known symbol."""

from __future__ import annotations

from collections import deque
from typing import Sequence

from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphIndex,
    RelatedHit,
    RelationKind,
)


def expand_related(
    index: CodeGraphIndex,
    symbol_id: str,
    *,
    relations: Sequence[str] | None = None,
    depth: int = 1,
    limit: int = 30,
) -> list[RelatedHit]:
    """Walk ``relations`` from ``symbol_id`` up to ``depth`` hops."""
    start = _resolve_symbol(index, symbol_id)
    if start is None:
        return []
    kinds = _parse_relations(relations)
    max_depth = max(1, depth)
    hits: list[RelatedHit] = []
    seen: set[tuple[str, str, int]] = set()
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    visited_nodes = {start}

    while queue and len(hits) < limit:
        current, current_depth = queue.popleft()
        if current_depth >= max_depth:
            continue
        for kind in kinds:
            for neighbor_id in index.neighbors(current, kind):
                neighbor = index.symbols.get(neighbor_id)
                if neighbor is None:
                    continue
                key = (neighbor_id, kind.value, current_depth + 1)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    RelatedHit(
                        symbol_id=neighbor.symbol_id,
                        name=neighbor.name,
                        kind=neighbor.kind.value,
                        file=neighbor.file,
                        start_line=neighbor.start_line,
                        end_line=neighbor.end_line,
                        relation=kind.value,
                        depth=current_depth + 1,
                        qualified_name=neighbor.qualified_name or neighbor.name,
                    )
                )
                if neighbor_id not in visited_nodes:
                    visited_nodes.add(neighbor_id)
                    queue.append((neighbor_id, current_depth + 1))
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
    return hits


def _resolve_symbol(index: CodeGraphIndex, symbol_id: str) -> str | None:
    if symbol_id in index.symbols:
        return symbol_id
    ids = index.by_name.get(symbol_id.lower(), [])
    if len(ids) == 1:
        return ids[0]
    return None


def _parse_relations(relations: Sequence[str] | None) -> list[RelationKind]:
    if not relations:
        return [
            RelationKind.CONTAINS,
            RelationKind.CONTAINED_BY,
            RelationKind.INHERITS,
            RelationKind.INHERITED_BY,
            RelationKind.CALLS,
            RelationKind.CALLED_BY,
            RelationKind.IMPORTS,
            RelationKind.IMPORTED_BY,
        ]
    parsed: list[RelationKind] = []
    for item in relations:
        try:
            parsed.append(RelationKind(item))
        except ValueError:
            continue
    return parsed
