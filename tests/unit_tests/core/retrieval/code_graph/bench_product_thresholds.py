# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Product-path thresholds the slice benches could not see.

Measures real ``build_index`` (lexical on), query latency, RSS, checkpoint
save/load, and same-byte dense vs sparse. Not collected by pytest.

    UV_NO_SYNC=1 uv run python tests/unit_tests/core/retrieval/code_graph/bench_product_thresholds.py \\
        /path/to/repo --out /tmp/thresholds.json
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from openjiuwen.core.retrieval.code_graph.indexing.builder import (
    _iter_source_files,
    build_index,
)
from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files
from openjiuwen.core.retrieval.code_graph.lifecycle import estimate_index_bytes
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig, SEARCHABLE_SYMBOL_KINDS
from openjiuwen.core.retrieval.code_graph.query.expand_related import expand_related
from openjiuwen.core.retrieval.code_graph.query.resolve_symbol import resolve_symbol
from openjiuwen.core.retrieval.code_graph.query.search_code import search_code
from openjiuwen.core.retrieval.code_graph.query.search_text import search_text
from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore
from openjiuwen.core.retrieval.code_graph.workspace_token import compute_workspace_token


def _rss_mb() -> float:
    try:
        out = os.popen(f"ps -o rss= -p {os.getpid()}").read().strip()
        return int(out) / 1024.0
    except Exception:
        return 0.0


def _wide_cfg(*, text: bool, definitions: bool = True) -> CodeGraphConfig:
    return CodeGraphConfig(
        cache_dir=None,
        max_files=100_000,
        max_source_bytes=512 * 1024 * 1024,
        index_definition_bodies=definitions,
        index_text_files=text,
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


def _median_ms(fn, repeats: int = 5) -> float:
    samples: list[float] = []
    fn()
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 3)


def _pick_query_symbol(index) -> tuple[str, str]:
    for symbol in index.symbols.values():
        if symbol.kind not in SEARCHABLE_SYMBOL_KINDS:
            continue
        if len(symbol.name) < 4:
            continue
        if symbol.name[0] == "_":
            continue
        return symbol.name, symbol.symbol_id
    return "Index", ""


def _summarize(index, *, build_s: float, rss_before: float) -> dict[str, object]:
    edges = len(index.relations)
    files = index.file_count or len(index.file_hashes)
    estimated = estimate_index_bytes(index)
    lexical = index.lexical
    return {
        "indexed_files": files,
        "source_files_hashed": len(index.file_hashes),
        "symbols": len(index.symbols),
        "relations": edges,
        "edges_per_file": round(edges / files, 2) if files else 0.0,
        "estimated_mb": round(estimated / (1024 * 1024), 2),
        "lexical_def_docs": len(lexical.definition_ids) if lexical is not None else 0,
        "lexical_text_docs": len(lexical.text_ids) if lexical is not None else 0,
        "build_s": round(build_s, 3),
        "rss_mb": round(_rss_mb(), 1),
        "rss_delta_mb": round(max(0.0, _rss_mb() - rss_before), 1),
    }


def _query_pack(index) -> dict[str, object]:
    name, symbol_id = _pick_query_symbol(index)
    pack = {
        "needle": name,
        "resolve_ms": _median_ms(lambda: resolve_symbol(index, name)),
        "search_code_ms": _median_ms(lambda: search_code(index, name, limit=10)),
        "search_text_ms": _median_ms(lambda: search_text(index, name, limit=10)),
    }
    if symbol_id:
        pack["callers_ms"] = _median_ms(
            lambda: expand_related(index, symbol_id, relations=["called_by"], limit=20)
        )
    return pack


def _checkpoint_pack(index) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="cg-ckpt-") as raw:
        store = DiskIndexStore(raw, max_size_mb=2048)
        started = time.perf_counter()
        store.save("bench", index)
        save_s = time.perf_counter() - started
        path = Path(raw) / "bench.pkl"
        pickle_mb = path.stat().st_size / (1024 * 1024) if path.is_file() else 0.0
        started = time.perf_counter()
        loaded = store.load("bench")
        load_s = time.perf_counter() - started
        ok = loaded is not None and len(loaded.symbols) == len(index.symbols)
        return {
            "save_s": round(save_s, 3),
            "load_s": round(load_s, 3),
            "pickle_mb": round(pickle_mb, 2),
            "roundtrip_ok": ok,
        }


def _incremental_one(index, root: Path, cfg: CodeGraphConfig) -> float | None:
    rels = [rel for rel in index.file_hashes if rel.endswith(".py")]
    if not rels:
        rels = list(index.file_hashes)
    if not rels:
        return None
    rel = rels[0]
    path = root / rel
    original = path.read_text(encoding="utf-8", errors="replace")
    try:
        path.write_text(original + f"\n# bench-product {time.time_ns()}\n", encoding="utf-8")
        copied = index.copy_for_session()
        started = time.perf_counter()
        refresh_index_files(copied, [rel], cfg)
        return round(time.perf_counter() - started, 3)
    finally:
        path.write_text(original, encoding="utf-8")


def _measure_tree(tree: Path, cfg: CodeGraphConfig, *, queries: bool, checkpoint: bool, incremental: bool) -> dict[str, object]:
    gc.collect()
    rss_before = _rss_mb()
    started = time.perf_counter()
    index = build_index(tree, cfg)
    payload = _summarize(index, build_s=time.perf_counter() - started, rss_before=rss_before)
    if queries:
        payload["queries"] = _query_pack(index)
    if checkpoint:
        payload["checkpoint"] = _checkpoint_pack(index)
    if incremental:
        payload["incremental_1_s"] = _incremental_one(index, tree, cfg)
    del index
    gc.collect()
    return payload


def _overlay(root: Path) -> dict[str, object]:
    walk_cfg = _wide_cfg(text=True)
    started = time.perf_counter()
    catalog = _iter_source_files(root, walk_cfg)
    walk_s = time.perf_counter() - started
    started = time.perf_counter()
    token = compute_workspace_token(root, walk_cfg)
    token_s = time.perf_counter() - started
    rows = {
        "walk_s": round(walk_s, 3),
        "token_s": round(token_s, 3),
        "token_dirty_paths": len(token.dirty_paths),
        "catalog_files": len(catalog),
        "catalog_mb": round(_source_bytes(catalog) / (1024 * 1024), 3),
        "variants": {},
    }
    for label, cfg in (
        ("no_lexical", _wide_cfg(text=False, definitions=False)),
        ("defs_only", _wide_cfg(text=False, definitions=True)),
        ("product", _wide_cfg(text=True, definitions=True)),
    ):
        rows["variants"][label] = _measure_tree(
            root,
            cfg,
            queries=label == "product",
            checkpoint=label == "product",
            incremental=False,
        )
    return rows


def _curve(root: Path, sizes: list[int]) -> list[dict[str, object]]:
    cfg = _wide_cfg(text=False, definitions=True)
    catalog = _iter_source_files(root, cfg)
    points: list[dict[str, object]] = []
    for size in sizes:
        files = catalog if size <= 0 or size >= len(catalog) else catalog[:size]
        with tempfile.TemporaryDirectory(prefix="cg-curve-") as raw:
            dest = Path(raw)
            _materialize(root, files, dest)
            point = _measure_tree(dest, cfg, queries=True, checkpoint=True, incremental=True)
            point["requested_files"] = len(files)
            point["source_mb"] = round(_source_bytes(files) / (1024 * 1024), 3)
            point["full_catalog"] = len(files) == len(catalog)
        points.append(point)
        print(json.dumps({"curve": point["requested_files"], "build_s": point["build_s"]}), flush=True)
    return points


def _byte_points(root: Path, budgets: list[int]) -> list[dict[str, object]]:
    cfg = _wide_cfg(text=False, definitions=True)
    catalog = _iter_source_files(root, cfg)
    points: list[dict[str, object]] = []
    for budget in budgets:
        files = _take_bytes(catalog, budget)
        with tempfile.TemporaryDirectory(prefix="cg-bytes-") as raw:
            dest = Path(raw)
            _materialize(root, files, dest)
            point = _measure_tree(dest, cfg, queries=True, checkpoint=False, incremental=True)
            point["byte_budget"] = budget
            point["requested_files"] = len(files)
            point["source_mb"] = round(_source_bytes(files) / (1024 * 1024), 3)
        points.append(point)
        print(json.dumps({"bytes": point["source_mb"], "build_s": point["build_s"]}), flush=True)
    return points


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--sizes", default="")
    parser.add_argument("--byte-budgets", default="8388608,16777216,33554432")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    sizes = [int(item) for item in str(args.sizes).split(",") if item.strip()]
    budgets = [int(item) for item in str(args.byte_budgets).split(",") if item.strip()]
    report = {
        "repo": str(root),
        "overlay": _overlay(root),
        "curve": _curve(root, sizes) if sizes else [],
        "byte_targets": _byte_points(root, budgets) if budgets else [],
    }
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
