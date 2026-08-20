# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Change surface of a symbol, grouped by responsibility.

Every group is derived from stored relations. Capabilities the graph does not
have (framework registration, dynamic dispatch) are reported as gaps rather
than filled in with heuristics, so a caller can trust what is returned.
"""

from __future__ import annotations

from collections import deque
from typing import Sequence

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphStatus, status_payload
from openjiuwen.core.retrieval.code_graph.models import (
    CLASS_LIKE_KINDS,
    CodeGraphIndex,
    RelationKind,
    Symbol,
)
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path
from openjiuwen.core.retrieval.code_graph.query.trace_call_chain import (
    DIRECTION_CALLERS,
    TraceLimits,
    resolve_call_symbol,
    trace_call_chain,
)

GROUP_CALLERS = "called_by"
GROUP_INHERITANCE = "inherited_by"
GROUP_IMPORTS = "imported_by"
VALID_GROUPS = (GROUP_CALLERS, GROUP_INHERITANCE, GROUP_IMPORTS)

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# Crossing this many top-level modules, or having this many independent
# implementors, means the change cannot be verified from one place.
HIGH_CALLER_COUNT = 10
HIGH_MODULE_COUNT = 3
HIGH_DERIVED_COUNT = 2
MEDIUM_CALLER_COUNT = 3
MEDIUM_MODULE_COUNT = 2

# The index has no relation for framework registries, entry-point tables, or
# dynamic dispatch, so this group can never be answered as COMPLETE.
REGISTRATION_WARNING = "registration relation unavailable"


def analyze_impact(
    index: CodeGraphIndex,
    symbol_id: str,
    *,
    max_depth: int = 3,
    max_nodes: int = 100,
    include_tests: bool = True,
    relations: Sequence[str] | None = None,
) -> dict[str, object]:
    """Report who is affected when ``symbol_id`` changes."""
    target, candidates = resolve_call_symbol(index, symbol_id)
    if target is None:
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
            message=f"no symbol for {symbol_id!r}",
            extra={"symbol_id": symbol_id, "index_snapshot": index.snapshot},
        )

    depth = max(1, min(int(max_depth), 10))
    budget = max(1, min(int(max_nodes), 1000))
    warnings: list[str] = []

    unresolved = [
        item.to_dict()
        for item in index.unresolved_calls
        if item.callee_name == target.name
    ][:budget]
    surface = impact_surface(
        index,
        target,
        depth=depth,
        budget=budget,
        include_tests=include_tests,
        groups=_requested_groups(relations),
        unresolved=unresolved,
    )
    truncated = bool(surface["truncated"])
    if unresolved:
        warnings.append(
            f"{len(unresolved)} call sites reference {target.name!r} but could not be resolved"
        )
    warnings.append(REGISTRATION_WARNING)
    if truncated:
        warnings.append("result truncated by depth or node limit")

    chain = trace_call_chain(
        index,
        target.symbol_id,
        direction=DIRECTION_CALLERS,
        limits=TraceLimits(max_depth=depth, max_paths=10, max_nodes=budget),
        include_tests=include_tests,
    )
    paths = chain.get("paths") if isinstance(chain, dict) else None

    # PARTIAL is unconditional: registrations are never available.
    return status_payload(
        CodeGraphStatus.PARTIAL,
        message=(
            f"impact of {target.symbol_id}: {len(surface['direct_callers'])} direct callers, "
            f"{len(surface['subclasses'])} subclasses, {len(surface['imports'])} importers"
        ),
        extra={
            "target": _node(target),
            "direct_callers": surface["direct_callers"],
            "transitive_callers": surface["transitive_callers"],
            "implementations": surface["implementations"],
            "subclasses": surface["subclasses"],
            "imports": surface["imports"],
            "registrations": [],
            "tests": surface["tests"],
            "paths": paths if isinstance(paths, list) else [],
            "risk": surface["risk"],
            "unresolved": unresolved,
            "truncated": truncated,
            "index_snapshot": index.snapshot,
            "warnings": warnings,
        },
    )


def impact_surface(
    index: CodeGraphIndex,
    target: Symbol,
    *,
    depth: int = 3,
    budget: int = 100,
    include_tests: bool = True,
    groups: set[str] | None = None,
    unresolved: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Change surface of one symbol: callers, inheritance, importers, tests, risk.

    Shared with patch-impact analysis so both answer "who is affected" with the
    same traversal and the same deterministic risk rules.
    """
    wanted = groups or set(VALID_GROUPS)
    truncated = False
    direct_callers: list[dict[str, object]] = []
    transitive_callers: list[dict[str, object]] = []
    if GROUP_CALLERS in wanted:
        levels, hit_limit = _bfs(
            index,
            target.symbol_id,
            RelationKind.CALLED_BY,
            depth=depth,
            budget=budget,
            include_tests=include_tests,
        )
        truncated = truncated or hit_limit
        direct_callers = levels.get(1, [])
        transitive_callers = [
            row for level in sorted(levels) if level > 1 for row in levels[level]
        ]

    subclasses: list[dict[str, object]] = []
    implementations: list[dict[str, object]] = []
    if GROUP_INHERITANCE in wanted:
        subclasses, implementations, hit_limit = _inheritance_surface(
            index,
            target,
            depth=depth,
            budget=budget,
            include_tests=include_tests,
        )
        truncated = truncated or hit_limit

    imports: list[dict[str, object]] = []
    if GROUP_IMPORTS in wanted:
        imports, hit_limit = _importers(index, target, budget=budget, include_tests=include_tests)
        truncated = truncated or hit_limit

    tests = _test_surface(index, target, direct_callers, transitive_callers)
    return {
        "direct_callers": direct_callers,
        "transitive_callers": transitive_callers,
        "subclasses": subclasses,
        "implementations": implementations,
        "imports": imports,
        "tests": tests,
        "truncated": truncated,
        "risk": _risk(
            target,
            direct_callers=direct_callers,
            transitive_callers=transitive_callers,
            subclasses=subclasses,
            implementations=implementations,
            imports=imports,
            tests=tests,
            truncated=truncated,
            unresolved=unresolved or [],
        ),
    }


def _requested_groups(relations: Sequence[str] | None) -> set[str]:
    if not relations:
        return set(VALID_GROUPS)
    wanted = {str(item).strip().lower() for item in relations}
    # Accept forward names too: asking for "calls" means "who calls this".
    aliases = {"calls": GROUP_CALLERS, "inherits": GROUP_INHERITANCE, "imports": GROUP_IMPORTS}
    resolved = {aliases.get(item, item) for item in wanted}
    return {item for item in resolved if item in VALID_GROUPS} or set(VALID_GROUPS)


def _bfs(
    index: CodeGraphIndex,
    start_id: str,
    relation: RelationKind,
    *,
    depth: int,
    budget: int,
    include_tests: bool,
) -> tuple[dict[int, list[dict[str, object]]], bool]:
    """Breadth-first neighbor collection, grouped by hop distance."""
    levels: dict[int, list[dict[str, object]]] = {}
    seen: set[str] = {start_id}
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    count = 0
    while queue:
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for neighbor_id in index.neighbors(current, relation):
            if neighbor_id in seen:
                continue
            neighbor = index.symbols.get(neighbor_id)
            if neighbor is None:
                continue
            seen.add(neighbor_id)
            if not include_tests and is_test_path(neighbor.file):
                continue
            if count >= budget:
                return levels, True
            row = _node(neighbor)
            row["depth"] = current_depth + 1
            row["via"] = current
            row["evidence"] = _edge_evidence(index, current, neighbor_id, relation)
            levels.setdefault(current_depth + 1, []).append(row)
            count += 1
            queue.append((neighbor_id, current_depth + 1))
    return levels, False


def _edge_evidence(
    index: CodeGraphIndex,
    current: str,
    neighbor_id: str,
    relation: RelationKind,
) -> dict[str, object] | None:
    evidence = index.evidence_for(current, relation, neighbor_id)
    if not evidence:
        return None
    best = max(evidence, key=lambda item: item.confidence)
    return best.to_dict()


def _inheritance_surface(
    index: CodeGraphIndex,
    target: Symbol,
    *,
    depth: int,
    budget: int,
    include_tests: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]], bool]:
    """Subclasses of the class, plus overrides when the target is a member."""
    owner = target if target.kind in CLASS_LIKE_KINDS else _owning_class(index, target)
    if owner is None:
        return [], [], False
    levels, truncated = _bfs(
        index,
        owner.symbol_id,
        RelationKind.INHERITED_BY,
        depth=depth,
        budget=budget,
        include_tests=include_tests,
    )
    subclasses = [row for level in sorted(levels) for row in levels[level]]
    if target.kind in CLASS_LIKE_KINDS:
        return subclasses, [], truncated
    overrides: list[dict[str, object]] = []
    for row in subclasses:
        member = _member_named(index, str(row["symbol_id"]), target.name)
        if member is None:
            continue
        entry = _node(member)
        entry["overrides"] = target.symbol_id
        entry["declaring_class"] = row["symbol_id"]
        overrides.append(entry)
    return subclasses, overrides, truncated


def _owning_class(index: CodeGraphIndex, symbol: Symbol) -> Symbol | None:
    parent = index.symbols.get(symbol.parent_id) if symbol.parent_id else None
    if parent is not None and parent.kind in CLASS_LIKE_KINDS:
        return parent
    return None


def _member_named(index: CodeGraphIndex, class_id: str, name: str) -> Symbol | None:
    for symbol_id in index.neighbors(class_id, RelationKind.CONTAINS):
        symbol = index.symbols.get(symbol_id)
        if symbol is not None and symbol.name == name:
            return symbol
    return None


def _importers(
    index: CodeGraphIndex,
    target: Symbol,
    *,
    budget: int,
    include_tests: bool,
) -> tuple[list[dict[str, object]], bool]:
    """Files importing the symbol directly or importing its file."""
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for source_id in (target.symbol_id, target.file):
        for importer_id in index.neighbors(source_id, RelationKind.IMPORTED_BY):
            if importer_id in seen:
                continue
            importer = index.symbols.get(importer_id)
            if importer is None:
                continue
            seen.add(importer_id)
            if not include_tests and is_test_path(importer.file):
                continue
            if len(rows) >= budget:
                return rows, True
            row = _node(importer)
            row["imported"] = source_id
            rows.append(row)
    return rows, False


def _test_surface(
    index: CodeGraphIndex,
    target: Symbol,
    direct_callers: list[dict[str, object]],
    transitive_callers: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Test-path callers and test files importing the target's file."""
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in (*direct_callers, *transitive_callers):
        path = str(row.get("file") or "")
        symbol_id = str(row.get("symbol_id") or "")
        if symbol_id in seen or not is_test_path(path):
            continue
        seen.add(symbol_id)
        rows.append({**row, "reason": "test code calls the target"})
    for importer_id in index.neighbors(target.file, RelationKind.IMPORTED_BY):
        importer = index.symbols.get(importer_id)
        if importer is None or importer_id in seen or not is_test_path(importer.file):
            continue
        seen.add(importer_id)
        entry = _node(importer)
        entry["reason"] = "test file imports the target file"
        rows.append(entry)
    return rows


def _risk(
    target: Symbol,
    *,
    direct_callers: list[dict[str, object]],
    transitive_callers: list[dict[str, object]],
    subclasses: list[dict[str, object]],
    implementations: list[dict[str, object]],
    imports: list[dict[str, object]],
    tests: list[dict[str, object]],
    truncated: bool,
    unresolved: list[dict[str, object]],
) -> dict[str, object]:
    """Deterministic rules only. No model call belongs in the core engine."""
    caller_count = len(direct_callers) + len(transitive_callers)
    # Only code that calls or overrides the target has to change with it.
    # Importers of the declaring file are context, not change surface.
    modules = {
        str(row.get("file") or "").split("/", 1)[0]
        for row in (*direct_callers, *transitive_callers, *implementations)
        if row.get("file")
    }
    # Subclasses of the owning class only widen the surface when the target is
    # the class itself; for a member, an actual override is what matters.
    derived = len(implementations) + (
        len(subclasses) if target.kind in CLASS_LIKE_KINDS else 0
    )
    reasons: list[str] = []
    if not target.name.startswith("_"):
        reasons.append("public symbol")
    if caller_count:
        reasons.append(f"{caller_count} callers")
    if derived:
        reasons.append(f"{derived} subclasses or overrides")
    if len(modules) > 1:
        reasons.append(f"spans {len(modules)} top-level modules")
    if imports:
        reasons.append(f"{len(imports)} importers")
    if tests:
        reasons.append(f"{len(tests)} test entry points")
    if unresolved:
        reasons.append(f"{len(unresolved)} unresolved call sites")
    if truncated:
        reasons.append("graph traversal truncated")

    if (
        caller_count >= HIGH_CALLER_COUNT
        or len(modules) >= HIGH_MODULE_COUNT
        or derived >= HIGH_DERIVED_COUNT
    ):
        level = RISK_HIGH
    elif (
        caller_count >= MEDIUM_CALLER_COUNT
        or len(modules) >= MEDIUM_MODULE_COUNT
        or derived
        or truncated
        or unresolved
    ):
        level = RISK_MEDIUM
    else:
        level = RISK_LOW
    return {"level": level, "reasons": reasons}


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
