# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Parent / subclass neighbors for a class-like symbol."""

from __future__ import annotations

from openjiuwen.core.retrieval.code_graph.models import (
    CLASS_LIKE_KINDS,
    CodeGraphIndex,
    RelationKind,
    Symbol,
)
from openjiuwen.core.retrieval.code_graph.query.expand_related import expand_related


def resolve_class_symbol(index: CodeGraphIndex, symbol_id: str) -> Symbol | None:
    """Resolve ``symbol_id`` to a class-like node, walking parents if needed."""
    current = index.symbols.get(symbol_id)
    if current is None:
        ids = index.by_name.get(symbol_id.lower(), [])
        if len(ids) != 1:
            return None
        current = index.symbols.get(ids[0])
    seen: set[str] = set()
    while current is not None and current.symbol_id not in seen:
        seen.add(current.symbol_id)
        if current.kind in CLASS_LIKE_KINDS:
            return current
        parent_id = current.parent_id
        current = index.symbols.get(parent_id) if parent_id else None
    return None


def expand_inheritance(
    index: CodeGraphIndex,
    symbol_id: str,
    *,
    limit: int = 30,
) -> tuple[Symbol | None, list[dict[str, object]]]:
    """Return (resolved class, inherit/inherited_by hits)."""
    klass = resolve_class_symbol(index, symbol_id)
    if klass is None:
        return None, []
    hits = expand_related(
        index,
        klass.symbol_id,
        relations=[RelationKind.INHERITS.value, RelationKind.INHERITED_BY.value],
        depth=1,
        limit=limit,
    )
    payload = []
    for item in hits:
        row = item.to_dict()
        row["source"] = klass.symbol_id
        payload.append(row)
    return klass, payload


def class_has_inheritance_neighbors(index: CodeGraphIndex, symbol_id: str) -> bool:
    klass = resolve_class_symbol(index, symbol_id)
    if klass is None:
        return False
    if index.neighbors(klass.symbol_id, RelationKind.INHERITS):
        return True
    return bool(index.neighbors(klass.symbol_id, RelationKind.INHERITED_BY))
