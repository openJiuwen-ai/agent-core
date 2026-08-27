# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Time-to-first-hit: wait for graph vs grep while an index is building.

Not collected by pytest.

    UV_NO_SYNC=1 uv run python tests/unit_tests/core/retrieval/code_graph/bench_wait_vs_grep.py \\
        /path/to/repo --sizes 200,400,800,1600 --waits 0.5,1,2,4,8
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections import Counter
from pathlib import Path

from openjiuwen.core.retrieval.code_graph.indexing.builder import (
    extract_one_file,
    resolve_relations,
)
from openjiuwen.core.retrieval.code_graph.indexing.language_registry import language_from_path
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphConfig,
    CodeGraphIndex,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot

_RG_CANDIDATES = (
    "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg",
    "rg",
)


def _rg_bin() -> str | None:
    for candidate in _RG_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return str(path)
        which = subprocess.run(["which", candidate], capture_output=True, text=True, check=False)
        if which.returncode == 0 and which.stdout.strip():
            return which.stdout.strip()
    return None


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


def _build_subset(root: Path, files: list[Path], cfg: CodeGraphConfig) -> tuple[CodeGraphIndex, float]:
    index = CodeGraphIndex(
        repo_root=str(root),
        snapshot=compute_snapshot(root),
        config_hash=cfg.config_hash(),
    )
    started = time.perf_counter()
    for path in files:
        rel = path.relative_to(root).as_posix()
        parsed = extract_one_file(path, rel, cfg)
        if parsed is None or parsed.oversized or parsed.extracted is None:
            continue
        index.extracted[rel] = parsed.extracted
        index.file_hashes[rel] = parsed.content_hash
        for symbol in parsed.extracted.symbols:
            index.add_symbol(symbol)
    resolve_relations(index)
    index.file_count = len(
        {sym.file for sym in index.symbols.values() if sym.kind == SymbolKind.FILE}
    )
    return index, time.perf_counter() - started


def _pick_symbol(index: CodeGraphIndex) -> str:
    counts: Counter[str] = Counter()
    preferred: list[str] = []
    for symbol in index.symbols.values():
        if symbol.kind not in {SymbolKind.CLASS, SymbolKind.FUNCTION, SymbolKind.METHOD}:
            continue
        if not symbol.name or symbol.name.startswith("_"):
            continue
        counts[symbol.name] += 1
        if symbol.kind == SymbolKind.CLASS:
            preferred.append(symbol.name)
    unique_class = [name for name in preferred if counts[name] == 1]
    if unique_class:
        return unique_class[0]
    unique = [name for name, count in counts.items() if count == 1]
    return unique[0] if unique else next(iter(counts), "class")


def _grep(rg: str, root: Path, files: list[Path], name: str) -> tuple[float, int]:
    listing = "\n".join(str(path) for path in files)
    started = time.perf_counter()
    completed = subprocess.run(
        [rg, "-n", "--no-heading", "-F", "--files-from", "-", name],
        cwd=root,
        input=listing,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - started
    hits = len([line for line in completed.stdout.splitlines() if line.strip()])
    return elapsed, hits


def _resolve(index: CodeGraphIndex, name: str) -> tuple[float, int]:
    started = time.perf_counter()
    hits = [item for item in index.symbols.values() if item.name == name]
    return time.perf_counter() - started, len(hits)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--sizes", default="200,400,800,1600")
    parser.add_argument("--waits", default="0.5,1,2,4,8")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    rg = _rg_bin()
    if rg is None:
        raise SystemExit("ripgrep not found")
    cfg = CodeGraphConfig(
        cache_dir=None,
        max_files=20000,
        max_source_bytes=256 * 1024 * 1024,
    )
    catalog = _iter_source_files(root, cfg)
    waits = [float(item) for item in str(args.waits).split(",") if item.strip()]
    report: dict[str, object] = {
        "repo": str(root),
        "catalog_source_files": len(catalog),
        "rg": rg,
        "points": [],
    }
    for raw_size in [int(item) for item in str(args.sizes).split(",") if item.strip()]:
        files = catalog if raw_size <= 0 or raw_size > len(catalog) else catalog[:raw_size]
        index, build_s = _build_subset(root, files, cfg)
        name = _pick_symbol(index)
        grep_s, grep_hits = _grep(rg, root, files, name)
        resolve_s, resolve_hits = _resolve(index, name)
        edges_per_file = (len(index.relations) / index.file_count) if index.file_count else 0.0
        symbols_per_file = (len(index.symbols) / index.file_count) if index.file_count else 0.0
        density = "dense" if edges_per_file >= 15 else ("sparse" if edges_per_file < 3 else "mid")
        graph_first_hit = build_s + resolve_s
        wait_rows = []
        for wait in waits:
            graph_ready = build_s <= wait
            wait_rows.append(
                {
                    "wait_s": wait,
                    "graph_ready_on_first_call": graph_ready,
                    "first_hit_if_block_until_ready_s": round(graph_first_hit, 3),
                    "first_hit_if_grep_now_s": round(grep_s, 3),
                    "grep_faster_than_blocking_for_graph": grep_s < graph_first_hit,
                }
            )
        point = {
            "indexed_files": index.file_count,
            "symbols": len(index.symbols),
            "relations": len(index.relations),
            "edges_per_file": round(edges_per_file, 2),
            "symbols_per_file": round(symbols_per_file, 2),
            "density": density,
            "query": name,
            "full_rebuild_s": round(build_s, 3),
            "grep_s": round(grep_s, 3),
            "grep_hits": grep_hits,
            "resolve_s": round(resolve_s, 6),
            "resolve_hits": resolve_hits,
            "waits": wait_rows,
        }
        report["points"].append(point)
        print(json.dumps(point), flush=True)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
