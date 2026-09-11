# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""List other definition symbols in the same file as a selected span."""

from __future__ import annotations

from openjiuwen.core.retrieval.code_graph.models import SEARCHABLE_SYMBOL_KINDS, CodeGraphIndex, Symbol
from openjiuwen.core.retrieval.code_graph.query.search_code import search_code


def expand_file_defs(
    index: CodeGraphIndex,
    file: str,
    query: str = "",
    *,
    limit: int = 30,
) -> list[dict[str, object]]:
    """Return definition-like symbols in ``file``, ranked by ``query`` when given."""
    rel = (file or "").replace("\\", "/").lstrip("./")
    if not rel:
        return []
    needle = (query or "").strip()
    if needle:
        hits = search_code(index, needle, path_prefix=rel, limit=max(1, limit) * 3, ban_tests=False)
        selected = [item for item in hits if item.file.replace("\\", "/") == rel]
        return [item.to_dict() for item in selected[: max(1, limit)]]
    symbols: list[Symbol] = []
    for symbol_id in index.by_file.get(rel, ()):
        symbol = index.symbols.get(symbol_id)
        if symbol is None or symbol.kind not in SEARCHABLE_SYMBOL_KINDS:
            continue
        symbols.append(symbol)
    symbols.sort(key=lambda item: (item.start_line, item.name))
    return [item.to_match(1.0).to_dict() for item in symbols[: max(1, limit)]]
