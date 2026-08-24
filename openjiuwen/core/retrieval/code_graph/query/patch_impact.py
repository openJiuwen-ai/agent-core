# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Graph-level review of an edit: what the patch changed in the graph.

This cannot prove the patch is semantically right. It answers the questions a
diff cannot: did a new file actually get wired in, did an edge disappear that
nothing asked to remove, which symbols now sit on the change surface, and which
tests reach them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.models import (
    FORWARD_RELATIONS,
    INVERSE_RELATIONS,
    CodeGraphIndex,
    RelationKind,
    Symbol,
)
from openjiuwen.core.retrieval.code_graph.query.analyze_impact import (
    REGISTRATION_WARNING,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    impact_surface,
)
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path

# A patch that rewrites more symbols than this is a refactor, not a fix: only the
# first few get a full impact query so one call cannot blow up the prompt.
MAX_FOCUS_SYMBOLS = 20
_WIRE_RELATIONS = (
    RelationKind.CALLED_BY,
    RelationKind.IMPORTED_BY,
    RelationKind.INHERITED_BY,
)


@dataclass(frozen=True)
class GraphSlice:
    """Symbols and incident edges of a set of files at one point in time.

    Only the changed files are captured. Keeping a whole second copy of the graph
    to diff against would double the memory of a large repository for no extra
    information.
    """

    files: tuple[str, ...] = ()
    snapshot: str = ""
    revision: int = 0
    symbols: dict[str, Symbol] = field(default_factory=dict)
    edges: frozenset[tuple[str, str, str]] = frozenset()
    file_hashes: dict[str, str] = field(default_factory=dict)


def capture_slice(index: CodeGraphIndex, files: list[str]) -> GraphSlice:
    """Snapshot the graph around ``files`` so a later edit can be diffed."""
    wanted = tuple(dict.fromkeys(files))
    symbols: dict[str, Symbol] = {}
    for file in wanted:
        for symbol_id in index.by_file.get(file, ()):
            symbol = index.symbols.get(symbol_id)
            if symbol is not None:
                symbols[symbol_id] = symbol
    edges: set[tuple[str, str, str]] = set()
    for symbol_id in symbols:
        for kind, targets in index.outgoing.get(symbol_id, {}).items():
            edges.update((symbol_id, kind, target) for target in targets)
        for kind, sources in index.incoming.get(symbol_id, {}).items():
            edges.update((source, _forward_name(kind), symbol_id) for source in sources)
    return GraphSlice(
        files=wanted,
        snapshot=index.snapshot,
        revision=index.revision,
        symbols=symbols,
        edges=frozenset(edges),
        file_hashes={file: index.file_hashes.get(file, "") for file in wanted},
    )


def analyze_patch_impact(
    index: CodeGraphIndex,
    before: GraphSlice,
    *,
    max_depth: int = 2,
    max_nodes: int = 60,
    include_tests: bool = True,
    focus_symbol_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """Diff the graph against ``before`` and report the patch's change surface."""
    depth = max(1, min(int(max_depth), 10))
    budget = max(1, min(int(max_nodes), 1000))
    after = capture_slice(index, list(before.files))

    added = [after.symbols[key] for key in after.symbols.keys() - before.symbols.keys()]
    removed = [before.symbols[key] for key in before.symbols.keys() - after.symbols.keys()]
    changed = [
        after.symbols[key]
        for key in after.symbols.keys() & before.symbols.keys()
        if _body_changed(before.symbols[key], after.symbols[key])
    ]
    added_edges = sorted(after.edges - before.edges)
    removed_edges = sorted(before.edges - after.edges)

    focus = _focus_symbols(added, changed, focus_symbol_ids)
    surfaces: list[dict[str, object]] = []
    tests: list[dict[str, object]] = []
    truncated = len(added) + len(changed) > MAX_FOCUS_SYMBOLS
    seen_tests: set[str] = set()
    for symbol in focus:
        surface = impact_surface(
            index,
            symbol,
            depth=depth,
            budget=budget,
            include_tests=include_tests,
        )
        truncated = truncated or bool(surface["truncated"])
        surfaces.append(
            {
                "symbol_id": symbol.symbol_id,
                "file": symbol.file,
                "direct_callers": surface["direct_callers"],
                "implementations": surface["implementations"],
                "imports": surface["imports"],
                "risk": surface["risk"],
            }
        )
        for row in surface["tests"]:  # type: ignore[union-attr]
            symbol_id = str(row.get("symbol_id") or "")
            if symbol_id and symbol_id not in seen_tests:
                seen_tests.add(symbol_id)
                tests.append(row)

    dangling = _dangling_references(index, before, after, removed)
    unwired = _unwired_symbols(index, added, tests)
    new_surface = [_node(symbol) for symbol in added if not symbol.name.startswith("_") and _is_api(symbol)]
    warnings = [REGISTRATION_WARNING]
    if index.stale_files:
        warnings.append("graph slice is stale for: " + ", ".join(sorted(index.stale_files)[:10]))
    if truncated:
        warnings.append("patch impact truncated by symbol or node limit")
    risk = _patch_risk(
        surfaces=surfaces,
        removed_edges=removed_edges,
        removed_symbols=removed,
        dangling=dangling,
        unwired=unwired,
        tests=tests,
        truncated=truncated,
    )
    return status_payload(
        CodeGraphStatus.PARTIAL,
        message=(
            f"patch touches {len(before.files)} file(s): {len(added)} added, "
            f"{len(changed)} changed, {len(removed)} removed symbols; "
            f"{len(tests)} candidate test(s)"
        ),
        extra={
            "files": list(before.files),
            "added_symbols": [_node(symbol) for symbol in added],
            "changed_symbols": [_node(symbol) for symbol in changed],
            "removed_symbols": [_node(symbol) for symbol in removed],
            "added_edges": [_edge(item) for item in added_edges],
            "removed_edges": [_edge(item) for item in removed_edges],
            "affected": surfaces,
            "test_candidates": tests,
            "dangling_references": dangling,
            "unwired_symbols": unwired,
            "new_public_surface": new_surface,
            "risk": risk,
            "truncated": truncated,
            "index_snapshot": index.snapshot,
            "index_revision": index.revision,
            "warnings": warnings,
        },
    )


def _forward_name(inverse_kind: str) -> str:
    """Normalize an adjacency key so an edge has one identity in both maps."""
    try:
        kind = RelationKind(inverse_kind)
    except ValueError:
        return inverse_kind
    if kind in FORWARD_RELATIONS:
        return kind.value
    return INVERSE_RELATIONS[kind].value


def _body_changed(before: Symbol, after: Symbol) -> bool:
    """A symbol counts as changed when its signature or its extent moved.

    Line shifts caused by an edit elsewhere in the file would otherwise mark the
    whole file as changed, so the start line alone is not enough.
    """
    return (
        before.signature != after.signature
        or (before.end_line - before.start_line) != (after.end_line - after.start_line)
        or before.parent_id != after.parent_id
    )


def _focus_symbols(
    added: list[Symbol],
    changed: list[Symbol],
    focus_symbol_ids: Sequence[str] | None = None,
) -> list[Symbol]:
    """Symbols worth an impact query: the intended ones, else every callable.

    Without a focus the file-level change surface includes every neighbour in
    the dirty file (``TimeSeries.fold`` after a ``_check_required_columns``
    edit), and the recommended tests follow the neighbour.
    """
    candidates = [symbol for symbol in (*changed, *added) if symbol.kind.value not in {"file", "module", "variable"}]
    wanted = [str(item).strip() for item in (focus_symbol_ids or []) if str(item).strip()]
    if wanted:
        matched = [symbol for symbol in candidates if _symbol_matches_focus(symbol, wanted)]
        if matched:
            return matched[:MAX_FOCUS_SYMBOLS]
    return candidates[:MAX_FOCUS_SYMBOLS]


def _symbol_matches_focus(symbol: Symbol, wanted: Sequence[str]) -> bool:
    sid = symbol.symbol_id.replace("\\", "/")
    name = symbol.name
    file = symbol.file.replace("\\", "/")
    for raw in wanted:
        token = str(raw).replace("\\", "/").strip()
        if not token:
            continue
        if token in {sid, name, file}:
            return True
        if _suffix_overlap(token, sid, name) and _file_matches_token_head(file, token):
            return True
    return False


def _suffix_overlap(token: str, sid: str, name: str) -> bool:
    return sid.endswith(token) or token.endswith(sid) or token.endswith(name)


def _file_matches_token_head(file: str, token: str) -> bool:
    head = token.split("::", 1)[0].split(":", 1)[0]
    if not head:
        return True
    if file.endswith(head) or head.endswith(file):
        return True
    return head in file


def _dangling_references(
    index: CodeGraphIndex,
    before: GraphSlice,
    after: GraphSlice,
    removed: list[Symbol],
) -> list[dict[str, object]]:
    """Edges that used to reach a now-missing symbol, plus new unresolved calls."""
    gone = {symbol.symbol_id for symbol in removed}
    rows: list[dict[str, object]] = []
    for source, kind, target in sorted(before.edges - after.edges):
        if target in gone and source not in gone:
            symbol = index.symbols.get(source)
            rows.append(
                {
                    "source_id": source,
                    "relation": kind,
                    "missing_target": target,
                    "file": symbol.file if symbol is not None else "",
                    "reason": "reference survives but its target was removed",
                }
            )
    changed_files = set(after.files)
    for call in index.unresolved_calls:
        if call.file in changed_files:
            row = call.to_dict()
            row["reason"] = "call in a changed file resolves to nothing in the graph"
            rows.append(row)
    return rows


def _unwired_symbols(
    index: CodeGraphIndex,
    added: list[Symbol],
    tests: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Added symbols nothing calls, imports, inherits, or tests."""
    tested_files = {str(row.get("file") or "") for row in tests}
    rows: list[dict[str, object]] = []
    for symbol in added:
        if symbol.kind.value in {"module", "variable"} or is_test_path(symbol.file):
            continue
        if _is_referenced(index, symbol.symbol_id) or symbol.file in tested_files:
            continue
        row = _node(symbol)
        row["reason"] = "added but nothing references it; check registration or import"
        rows.append(row)
    return rows


def _is_referenced(index: CodeGraphIndex, symbol_id: str) -> bool:
    for relation in _WIRE_RELATIONS:
        if index.neighbors(symbol_id, relation):
            return True
    return False


def _is_api(symbol: Symbol) -> bool:
    return symbol.kind.value in {"class", "function", "method", "interface", "struct", "trait"}


def _patch_risk(
    *,
    surfaces: list[dict[str, object]],
    removed_edges: list[tuple[str, str, str]],
    removed_symbols: list[Symbol],
    dangling: list[dict[str, object]],
    unwired: list[dict[str, object]],
    tests: list[dict[str, object]],
    truncated: bool,
) -> dict[str, object]:
    """Deterministic rules only, so two runs of the same patch agree."""
    levels = {str(item.get("risk", {}).get("level")) for item in surfaces}  # type: ignore[union-attr]
    reasons: list[str] = []
    if dangling:
        reasons.append(f"{len(dangling)} dangling or unresolved references")
    if unwired:
        reasons.append(f"{len(unwired)} added symbols nothing references")
    if removed_symbols:
        reasons.append(f"{len(removed_symbols)} symbols removed")
    if removed_edges:
        reasons.append(f"{len(removed_edges)} relations removed")
    if not tests:
        reasons.append("no test reaches the changed symbols")
    if truncated:
        reasons.append("analysis truncated")
    if RISK_HIGH in levels:
        reasons.append("a changed symbol has a high-risk change surface")

    if _patch_risk_high(dangling, levels, removed_symbols, tests):
        level = RISK_HIGH
    elif _patch_risk_medium(unwired, removed_edges, tests, levels, truncated):
        level = RISK_MEDIUM
    else:
        level = RISK_LOW
    return {"level": level, "reasons": reasons}


def _patch_risk_high(
    dangling: list[dict[str, object]],
    levels: set[str],
    removed_symbols: list[Symbol],
    tests: list[dict[str, object]],
) -> bool:
    if dangling:
        return True
    if RISK_HIGH in levels:
        return True
    return bool(removed_symbols) and not tests


def _patch_risk_medium(
    unwired: list[dict[str, object]],
    removed_edges: list[tuple[str, str, str]],
    tests: list[dict[str, object]],
    levels: set[str],
    truncated: bool,
) -> bool:
    if unwired or removed_edges or not tests:
        return True
    return RISK_MEDIUM in levels or truncated


def _edge(item: tuple[str, str, str]) -> dict[str, object]:
    source, kind, target = item
    return {"source_id": source, "relation": kind, "target_id": target}


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
