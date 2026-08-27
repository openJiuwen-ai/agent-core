# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Find incremental vs full-rebuild break-even by repository size.

Not collected by pytest. Indexes the first N source files of a real tree so
the size axis is comparable. Times parse vs relation-resolve separately.

    UV_NO_SYNC=1 uv run python tests/unit_tests/core/retrieval/code_graph/bench_scale.py \\
        /path/to/repo --sizes 50,100,200,400,800,1600 --edits 1,5,10
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
from openjiuwen.core.retrieval.code_graph.query.lexical import LexicalIndexBuilder
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


def _build_subset(
    root: Path,
    files: list[Path],
    cfg: CodeGraphConfig,
) -> tuple[CodeGraphIndex, float, float]:
    index = CodeGraphIndex(
        repo_root=str(root),
        snapshot=compute_snapshot(root),
        config_hash=cfg.config_hash(),
    )
    lexical = LexicalIndexBuilder()
    parse_started = time.perf_counter()
    for path in files:
        rel = path.relative_to(root).as_posix()
        parsed = extract_one_file(path, rel, cfg)
        if parsed is None or parsed.oversized or parsed.extracted is None:
            continue
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
    index.lexical = lexical.freeze()
    return index, parse_s, resolve_s


def _touch(root: Path, rel: str) -> str:
    path = root / rel
    original = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(original + f"\n# bench-scale {time.time_ns()}\n", encoding="utf-8")
    return original


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--sizes", default="50,100,200,400,800,1600")
    parser.add_argument("--edits", default="1,5,10")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    cfg = CodeGraphConfig(
        cache_dir=None,
        max_files=20000,
        max_source_bytes=256 * 1024 * 1024,
    )
    catalog = _iter_source_files(root, cfg)
    sizes = [int(item) for item in str(args.sizes).split(",") if item.strip()]
    edits = [int(item) for item in str(args.edits).split(",") if item.strip()]
    report: dict[str, object] = {
        "repo": str(root),
        "catalog_source_files": len(catalog),
        "points": [],
    }
    for size in sizes:
        if size <= 0 or size > len(catalog):
            files = catalog
            size = len(catalog)
        else:
            files = catalog[:size]
        index, parse_s, resolve_s = _build_subset(root, files, cfg)
        rels = list(index.file_hashes)
        point = {
            "requested_files": size,
            "indexed_files": index.file_count,
            "symbols": len(index.symbols),
            "relations": len(index.relations),
            "parse_s": round(parse_s, 3),
            "resolve_s": round(resolve_s, 3),
            "full_rebuild_s": round(parse_s + resolve_s, 3),
            "incr_over_full_1file": None,
            "edits": [],
        }
        edit_counts = list(edits)
        if size <= 400 and size not in edit_counts:
            edit_counts.append(size)
        for count in edit_counts:
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
            full_s = parse_s + resolve_s
            ratio = (incremental_s / full_s) if full_s > 0 else None
            row = {
                "changed_files": count,
                "incremental_s": round(incremental_s, 3),
                "ratio_vs_full": round(ratio, 3) if ratio is not None else None,
                "incremental_slower": bool(ratio is not None and ratio >= 1.0),
            }
            point["edits"].append(row)
            if count == 1:
                point["incr_over_full_1file"] = row["ratio_vs_full"]
        report["points"].append(point)
        print(json.dumps(point), flush=True)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
