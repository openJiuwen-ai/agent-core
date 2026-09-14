# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic symbol lookup without BM25."""

from __future__ import annotations

from openjiuwen.core.retrieval.code_graph.models import (
    SEARCHABLE_SYMBOL_KINDS,
    CodeGraphIndex,
    CodeMatch,
    Symbol,
)
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path


def strip_file_uri(name: str) -> str:
    """Drop a ``file://`` prefix the model sometimes copies from URIs."""
    needle = (name or "").strip()
    if needle.startswith("file://"):
        return needle[7:]
    return needle


def resolve_symbol(
    index: CodeGraphIndex,
    name: str,
    *,
    kind: str | None = None,
    path_hint: str | None = None,
    limit: int = 8,
) -> list[CodeMatch]:
    """Resolve ``name`` to definition symbols, exact matches first."""
    needle = strip_file_uri(name)
    if not needle:
        return []
    if needle in index.symbols:
        symbol = index.symbols[needle]
        if symbol.kind in SEARCHABLE_SYMBOL_KINDS:
            return [symbol.to_match(1.0)]

    kind_filter = (kind or "").strip().lower()
    prefix = path_hint.replace("\\", "/").lstrip("./") if path_hint else None
    lowered = needle.lower()
    qualified: list[Symbol] = []
    named: list[Symbol] = []
    for symbol in index.symbols.values():
        if symbol.kind not in SEARCHABLE_SYMBOL_KINDS:
            continue
        if kind_filter and symbol.kind.value != kind_filter:
            continue
        file_path = symbol.file.replace("\\", "/")
        if prefix and prefix not in file_path:
            continue
        if is_test_path(file_path) and "test" not in lowered:
            continue
        if (symbol.qualified_name or "").lower() == lowered or symbol.symbol_id.lower() == lowered:
            qualified.append(symbol)
        elif symbol.name.lower() == lowered:
            named.append(symbol)
    ordered = qualified + [item for item in named if item.symbol_id not in {s.symbol_id for s in qualified}]
    return [item.to_match(1.0 if item in qualified else 0.95) for item in ordered[: max(1, limit)]]
