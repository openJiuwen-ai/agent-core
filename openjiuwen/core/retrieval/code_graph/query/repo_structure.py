# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Query-scored repository tree from an already-built index."""

from __future__ import annotations

from collections import defaultdict

from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex
from openjiuwen.core.retrieval.code_graph.query.search_code import search_code
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path


def get_repo_structure(
    index: CodeGraphIndex,
    query: str = "",
    *,
    limit: int = 40,
    ban_tests: bool = True,
) -> dict[str, object]:
    """Return top-level entries plus optional query-focused files.

    Uses the in-memory symbol table only. Does not walk the working tree.
    """
    files = sorted({path.replace("\\", "/") for path in index.by_file})
    if ban_tests:
        files = [path for path in files if not is_test_path(path)]
    roots: dict[str, str] = {}
    for path in files:
        head = path.split("/", 1)[0]
        kind = "file" if "/" not in path else "dir"
        roots.setdefault(head, kind)
        if kind == "dir":
            roots[head] = "dir"
    root_entries = [{"name": name, "kind": kind} for name, kind in sorted(roots.items())]
    focus: list[dict[str, object]] = []
    needle = (query or "").strip()
    if needle:
        matches = search_code(index, needle, limit=max(1, limit), ban_tests=ban_tests)
        grouped: dict[str, list[str]] = defaultdict(list)
        scores: dict[str, float] = {}
        for match in matches:
            grouped[match.file].append(match.name)
            scores[match.file] = max(scores.get(match.file, 0.0), float(match.score))
        ranked_files = sorted(scores, key=lambda path: (-scores[path], path))
        for path in ranked_files[: max(1, limit)]:
            names = grouped[path]
            focus.append(
                {
                    "file": path,
                    "score": scores[path],
                    "symbols": names[:12],
                }
            )
    return {
        "roots": root_entries,
        "focus": focus,
        "file_count": len(files),
    }
