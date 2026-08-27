# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compare three graph/grep policies on first build and after an edit.

Not collected by pytest.

    UV_NO_SYNC=1 uv run python tests/unit_tests/core/retrieval/code_graph/bench_policy_compare.py \\
        /path/to/repo --out /tmp/policy.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from openjiuwen.core.retrieval.code_graph.indexing.builder import (
    _iter_source_files,
    build_index,
)
from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphConfig,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.query.expand_related import expand_related
from openjiuwen.core.retrieval.code_graph.query.resolve_symbol import resolve_symbol

_RG_CANDIDATES = (
    "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg",
    "rg",
)


def _rg_bin() -> str:
    for candidate in _RG_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return str(path)
        which = subprocess.run(["which", candidate], capture_output=True, text=True, check=False)
        if which.returncode == 0 and which.stdout.strip():
            return which.stdout.strip()
    raise SystemExit("ripgrep not found")


def _wide_cfg() -> CodeGraphConfig:
    return CodeGraphConfig(
        cache_dir=None,
        max_files=100_000,
        max_source_bytes=512 * 1024 * 1024,
        index_definition_bodies=True,
        index_text_files=False,
    )


def _source_bytes(files: list[Path]) -> int:
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _take_bytes(files: list[Path], budget: int) -> list[Path]:
    chosen: list[Path] = []
    total = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        chosen.append(path)
        total += size
        if total >= budget:
            break
    return chosen


def _materialize(root: Path, files: list[Path], dest: Path) -> None:
    for path in files:
        rel = path.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _grep(rg: str, tree: Path, name: str) -> tuple[float, int]:
    started = time.perf_counter()
    completed = subprocess.run(
        [rg, "-n", "--no-heading", "-F", name, "."],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    hits = len([line for line in completed.stdout.splitlines() if line.strip()])
    return time.perf_counter() - started, hits


def _resolve_hits(index, name: str) -> tuple[float, int]:
    started = time.perf_counter()
    hits = resolve_symbol(index, name)
    return time.perf_counter() - started, len(hits)


def _pick_unique_symbol(index) -> str | None:
    counts: Counter[str] = Counter()
    names: list[str] = []
    for symbol in index.symbols.values():
        if symbol.kind not in {SymbolKind.CLASS, SymbolKind.FUNCTION, SymbolKind.METHOD}:
            continue
        if not symbol.name or symbol.name.startswith("_"):
            continue
        if len(symbol.name) < 5:
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]+$", symbol.name):
            continue
        counts[symbol.name] += 1
        names.append(symbol.name)
    unique_class = [
        name
        for name in names
        if counts[name] == 1 and re.match(r"^[A-Z]", name)
    ]
    if unique_class:
        return unique_class[0]
    unique = [name for name in names if counts[name] == 1]
    return unique[0] if unique else None


def _pick_callee(index) -> dict[str, object] | None:
    for symbol in index.symbols.values():
        if symbol.kind not in {SymbolKind.FUNCTION, SymbolKind.METHOD}:
            continue
        if symbol.name.startswith("_"):
            continue
        callers = expand_related(index, symbol.symbol_id, relations=["called_by"], limit=20)
        if callers:
            return {"name": symbol.name, "symbol_id": symbol.symbol_id, "callers": len(callers)}
    return None


def _rename(tree: Path, old: str, new: str) -> str:
    changed = ""
    pattern = re.compile(rf"\b{re.escape(old)}\b")
    for path in tree.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".ts", ".js", ".go", ".java", ".rs"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if old not in text:
            continue
        updated = pattern.sub(new, text)
        if updated == text:
            continue
        path.write_text(updated, encoding="utf-8")
        changed = path.relative_to(tree).as_posix()
        break
    if not changed:
        raise RuntimeError(f"could not rename {old}")
    return changed


def _score_first(*, build_s: float, grep_s: float, grep_hits: int, resolve_hits: int, t_max: float) -> dict[str, object]:
    admitted = build_s <= t_max
    m1 = {
        "tool": "grep",
        "wait_s": round(grep_s, 4),
        "hits": grep_hits,
        "found": grep_hits > 0,
        "relations_available": False,
        "tool_flips_later": 1,
    }
    if admitted:
        m2 = {
            "tool": "graph",
            "wait_s": round(build_s, 3),
            "hits": resolve_hits,
            "found": resolve_hits > 0,
            "relations_available": True,
            "tool_flips_later": 0,
        }
    else:
        m2 = {
            "tool": "grep_forever",
            "wait_s": round(grep_s, 4),
            "hits": grep_hits,
            "found": grep_hits > 0,
            "relations_available": False,
            "tool_flips_later": 0,
        }
    return {"m1_grep_then_graph": m1, "m2_wait_or_refuse": m2, "m3_same_as_m2_on_first": m2}


def _score_rename(
    *,
    old: str,
    new: str,
    refresh_s: float,
    grep_new_s: float,
    grep_new_hits: int,
    stale_old_hits: int,
    stale_new_hits: int,
    fresh_new_hits: int,
    t_max: float,
    build_s: float,
) -> dict[str, object]:
    admitted = build_s <= t_max
    m1 = {
        "answer": "grep_new_name",
        "wait_s": round(grep_new_s, 4),
        "correct_new_name": grep_new_hits > 0,
        "wrong_old_name": False,
        "relations_available": False,
        "tool_flips": 1,
    }
    if admitted:
        m2 = {
            "answer": "wait_then_new_graph",
            "wait_s": round(refresh_s, 3),
            "correct_new_name": fresh_new_hits > 0,
            "wrong_old_name": False,
            "relations_available": True,
            "tool_flips": 0,
        }
        m3 = {
            "answer": "old_graph_immediately",
            "wait_s": 0.0,
            "correct_new_name": stale_new_hits > 0,
            "wrong_old_name": stale_old_hits > 0,
            "relations_available": True,
            "tool_flips": 0,
        }
    else:
        refuse = {
            "answer": "grep_forever",
            "wait_s": round(grep_new_s, 4),
            "correct_new_name": grep_new_hits > 0,
            "wrong_old_name": False,
            "relations_available": False,
            "tool_flips": 0,
        }
        m2 = refuse
        m3 = refuse
    return {"m1_grep_then_graph": m1, "m2_wait_or_refuse": m2, "m3_old_graph": m3, "admitted": admitted}


def _run_point(root: Path, files: list[Path], rg: str, thresholds: list[float]) -> dict[str, object]:
    cfg = _wide_cfg()
    with tempfile.TemporaryDirectory(prefix="cg-policy-") as raw:
        tree = Path(raw)
        _materialize(root, files, tree)
        started = time.perf_counter()
        index = build_index(tree, cfg)
        build_s = time.perf_counter() - started
        old = _pick_unique_symbol(index)
        if old is None:
            raise RuntimeError("no unique symbol in subset")
        new = old + "RenamedBench"
        grep_s, grep_hits = _grep(rg, tree, old)
        resolve_s, resolve_hits = _resolve_hits(index, old)
        callee = _pick_callee(index)
        first = {f"t_{int(t)}s": _score_first(
            build_s=build_s,
            grep_s=grep_s,
            grep_hits=grep_hits,
            resolve_hits=resolve_hits,
            t_max=t,
        ) for t in thresholds}

        rel = _rename(tree, old, new)
        grep_new_s, grep_new_hits = _grep(rg, tree, new)
        stale_old_s, stale_old_hits = _resolve_hits(index, old)
        stale_new_s, stale_new_hits = _resolve_hits(index, new)
        copied = index.copy_for_session()
        started = time.perf_counter()
        refresh_index_files(copied, [rel], cfg)
        refresh_s = time.perf_counter() - started
        _, fresh_new_hits = _resolve_hits(copied, new)
        _, fresh_old_hits = _resolve_hits(copied, old)
        callers_grep = 0
        if callee is not None:
            callers_grep = _grep(rg, tree, str(callee["name"]))[1]

        rename = {f"t_{int(t)}s": _score_rename(
            old=old,
            new=new,
            refresh_s=refresh_s,
            grep_new_s=grep_new_s,
            grep_new_hits=grep_new_hits,
            stale_old_hits=stale_old_hits,
            stale_new_hits=stale_new_hits,
            fresh_new_hits=fresh_new_hits,
            t_max=t,
            build_s=build_s,
        ) for t in thresholds}

        return {
            "requested_files": len(files),
            "indexed_files": index.file_count,
            "source_mb": round(_source_bytes(files) / (1024 * 1024), 3),
            "symbols": len(index.symbols),
            "relations": len(index.relations),
            "build_s": round(build_s, 3),
            "refresh_1_s": round(refresh_s, 3),
            "query": old,
            "renamed_to": new,
            "renamed_file": rel,
            "first_grep_s": round(grep_s, 4),
            "first_grep_hits": grep_hits,
            "first_resolve_s": round(resolve_s, 6),
            "first_resolve_hits": resolve_hits,
            "stale_still_has_old": stale_old_hits > 0,
            "stale_has_new": stale_new_hits > 0,
            "fresh_has_new": fresh_new_hits > 0,
            "fresh_still_has_old": fresh_old_hits > 0,
            "callee": None if callee is None else {
                "name": callee["name"],
                "graph_callers": callee["callers"],
                "grep_name_hits": callers_grep,
                "grep_is_not_callers": True,
            },
            "first_build": first,
            "after_rename": rename,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--sizes", default="400,1600")
    parser.add_argument("--byte-budgets", default="16777216,33554432")
    parser.add_argument("--thresholds", default="8,30")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    rg = _rg_bin()
    catalog = _iter_source_files(root, _wide_cfg())
    thresholds = [float(item) for item in str(args.thresholds).split(",") if item.strip()]
    points: list[dict[str, object]] = []
    for raw in [int(item) for item in str(args.sizes).split(",") if item.strip()]:
        files = catalog if raw <= 0 or raw >= len(catalog) else catalog[:raw]
        point = _run_point(root, files, rg, thresholds)
        point["kind"] = "prefix"
        points.append(point)
        print(json.dumps({"kind": "prefix", "files": point["indexed_files"], "build_s": point["build_s"]}), flush=True)
    for budget in [int(item) for item in str(args.byte_budgets).split(",") if item.strip()]:
        files = _take_bytes(catalog, budget)
        point = _run_point(root, files, rg, thresholds)
        point["kind"] = "bytes"
        points.append(point)
        print(json.dumps({"kind": "bytes", "mb": point["source_mb"], "build_s": point["build_s"]}), flush=True)
    report = {
        "repo": str(root),
        "catalog_files": len(catalog),
        "thresholds_s": thresholds,
        "points": points,
    }
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
