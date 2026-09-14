# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Map prefix size -> full-index seconds and source bytes.

Used to turn a time budget into file/byte admission caps.

    UV_NO_SYNC=1 uv run python tests/unit_tests/core/retrieval/code_graph/bench_time_budget.py \\
        /path/to/repo --sizes 50,100,200,400,800
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from openjiuwen.core.retrieval.code_graph.indexing.builder import (
    extract_one_file,
    resolve_relations,
)
from openjiuwen.core.retrieval.code_graph.indexing.language_registry import language_from_path
from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphConfig,
    CodeGraphIndex,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot


def _iter_source_files(root: Path, cfg: CodeGraphConfig) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not cfg.excludes_dir_name(name)]
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            try:
                if path.stat().st_size > cfg.max_file_bytes:
                    continue
            except OSError:
                continue
            if language_from_path(path) is None:
                continue
            files.append(path)
    files.sort()
    return files


def _build(root: Path, files: list[Path], cfg: CodeGraphConfig) -> tuple[CodeGraphIndex, float, float, int]:
    index = CodeGraphIndex(
        repo_root=str(root),
        snapshot=compute_snapshot(root),
        config_hash=cfg.config_hash(),
    )
    source_bytes = 0
    parse_started = time.perf_counter()
    for path in files:
        rel = path.relative_to(root).as_posix()
        parsed = extract_one_file(path, rel, cfg)
        if parsed is None or parsed.oversized or parsed.extracted is None:
            continue
        source_bytes += len(parsed.text.encode("utf-8", errors="replace"))
        index.extracted[rel] = parsed.extracted
        index.file_hashes[rel] = parsed.content_hash
        for symbol in parsed.extracted.symbols:
            index.add_symbol(symbol)
    parse_s = time.perf_counter() - parse_started
    resolve_started = time.perf_counter()
    resolve_relations(index)
    resolve_s = time.perf_counter() - resolve_started
    index.file_count = len(
        {sym.file for sym in index.symbols.values() if sym.kind == SymbolKind.FILE}
    )
    return index, parse_s, resolve_s, source_bytes


def _touch(root: Path, rel: str) -> str:
    path = root / rel
    original = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(original + f"\n# bench-time {time.time_ns()}\n", encoding="utf-8")
    return original


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--sizes", default="50,100,150,200,250,300,400,600,800")
    parser.add_argument("--edits", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    cfg = CodeGraphConfig(
        cache_dir=None,
        max_files=20000,
        max_source_bytes=256 * 1024 * 1024,
    )
    catalog = _iter_source_files(root, cfg)
    edits = [int(item) for item in str(args.edits).split(",") if item.strip()]
    report: dict[str, object] = {
        "repo": str(root),
        "catalog_source_files": len(catalog),
        "points": [],
    }
    for size in [int(item) for item in str(args.sizes).split(",") if item.strip()]:
        files = catalog if size <= 0 or size > len(catalog) else catalog[:size]
        index, parse_s, resolve_s, source_bytes = _build(root, files, cfg)
        full_s = parse_s + resolve_s
        edges_per_file = (len(index.relations) / index.file_count) if index.file_count else 0.0
        point: dict[str, object] = {
            "requested_files": size if size <= len(catalog) else len(catalog),
            "indexed_files": index.file_count,
            "source_bytes": source_bytes,
            "source_mb": round(source_bytes / (1024 * 1024), 3),
            "symbols": len(index.symbols),
            "relations": len(index.relations),
            "edges_per_file": round(edges_per_file, 2),
            "density": "dense" if edges_per_file >= 15 else ("sparse" if edges_per_file < 3 else "mid"),
            "parse_s": round(parse_s, 3),
            "resolve_s": round(resolve_s, 3),
            "full_rebuild_s": round(full_s, 3),
            "edits": [],
        }
        rels = list(index.file_hashes)
        for count in edits:
            if count < 1 or count > len(rels):
                continue
            paths = rels[:count]
            originals = {}
            try:
                for rel in paths:
                    originals[rel] = _touch(root, rel)
                copied = index.copy_for_session()
                started = time.perf_counter()
                refresh_index_files(copied, paths, cfg)
                incremental_s = time.perf_counter() - started
            finally:
                for rel, text in originals.items():
                    (root / rel).write_text(text, encoding="utf-8")
            ratio = incremental_s / full_s if full_s else None
            point["edits"].append(
                {
                    "changed_files": count,
                    "incremental_s": round(incremental_s, 3),
                    "ratio_vs_full": round(ratio, 3) if ratio is not None else None,
                    "incremental_slower": bool(ratio is not None and ratio >= 1.0),
                }
            )
        report["points"].append(point)
        print(json.dumps(point), flush=True)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
