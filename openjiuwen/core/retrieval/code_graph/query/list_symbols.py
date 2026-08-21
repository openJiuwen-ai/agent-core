# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""List semantic children of a file, class, or other parent symbol."""

from __future__ import annotations

from typing import Sequence

from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphIndex,
    CodeMatch,
    RelationKind,
    Symbol,
    SymbolKind,
)


def list_symbols(
    index: CodeGraphIndex,
    *,
    file: str | None = None,
    parent_symbol: str | None = None,
    kinds: Sequence[str] | None = None,
    depth: int = 1,
    limit: int = 100,
) -> list[CodeMatch]:
    """Return contained symbols under ``file`` / ``parent_symbol``."""
    kind_filter = {item.lower() for item in kinds} if kinds else None
    roots = _resolve_roots(index, file=file, parent_symbol=parent_symbol)
    if not roots:
        return []

    collected: list[Symbol] = []
    seen: set[str] = set()
    frontier = [(root, 0) for root in roots]
    max_depth = max(0, depth)
    while frontier:
        current_id, current_depth = frontier.pop()
        if current_depth >= max_depth:
            continue
        for child_id in index.neighbors(current_id, RelationKind.CONTAINS):
            if child_id in seen:
                continue
            seen.add(child_id)
            child = index.symbols.get(child_id)
            if child is None:
                continue
            if child.kind != SymbolKind.FILE:
                collected.append(child)
            frontier.append((child_id, current_depth + 1))

    matches: list[CodeMatch] = []
    for symbol in collected:
        if kind_filter is not None and symbol.kind.value not in kind_filter:
            continue
        matches.append(symbol.to_match(1.0))
    matches.sort(key=lambda item: (item.file, item.start_line, item.name))
    return matches[: max(1, limit)]


def _resolve_roots(
    index: CodeGraphIndex,
    *,
    file: str | None,
    parent_symbol: str | None,
) -> list[str]:
    if parent_symbol:
        if parent_symbol in index.symbols:
            return [parent_symbol]
        ids = index.by_name.get(parent_symbol.lower(), [])
        if file:
            ids = [sid for sid in ids if index.symbols[sid].file.replace("\\", "/") == file.replace("\\", "/")]
        return list(ids)
    if file:
        normalized = file.replace("\\", "/")
        if normalized in index.symbols:
            return [normalized]
        return [
            sid
            for sid, symbol in index.symbols.items()
            if symbol.kind == SymbolKind.FILE and symbol.file.replace("\\", "/") == normalized
        ]
    return []
