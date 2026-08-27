# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manual timings: incremental vs full refresh on a real tree.

Not collected by pytest. Run:

    uv run python tests/unit_tests/core/retrieval/code_graph/bench_refresh.py \\
        /path/to/repo --edits 1,10,100
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig


def _rss_mb() -> float:
    try:
        import resource
        import sys

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        bytes_ = int(usage) if sys.platform == "darwin" else int(usage) * 1024
        return bytes_ / (1024 * 1024)
    except Exception:
        return 0.0


def _pick_python_files(root: Path, limit: int) -> list[str]:
    files: list[str] = []
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".worktrees"}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in skip]
        for name in filenames:
            if name.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/")
                files.append(rel)
                if len(files) >= limit:
                    return files
    return files


def _touch(root: Path, rel: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(text + "\n# bench-touch\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--edits", default="1,10,100")
    parser.add_argument("--max-files", type=int, default=20000)
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    cfg = CodeGraphConfig(cache_dir=None, max_files=args.max_files)
    started = time.perf_counter()
    rss_before = _rss_mb()
    index = build_index(root, cfg)
    first_s = time.perf_counter() - started
    report = {
        "repo": str(root),
        "files_indexed": index.file_count,
        "symbols": len(index.symbols),
        "relations": len(index.relations),
        "first_build_s": round(first_s, 3),
        "rss_delta_mb": round(_rss_mb() - rss_before, 1),
        "edits": [],
    }
    counts = [int(item) for item in str(args.edits).split(",") if item.strip()]
    candidates = _pick_python_files(root, max(counts) if counts else 1)
    for count in counts:
        paths = candidates[:count]
        if len(paths) < count:
            break
        originals = {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in paths}
        try:
            for rel in paths:
                _touch(root, rel)
            copied = index.copy_for_session()
            inc_started = time.perf_counter()
            refresh_index_files(copied, paths, cfg)
            incremental_s = time.perf_counter() - inc_started
            full_started = time.perf_counter()
            build_index(root, cfg)
            full_s = time.perf_counter() - full_started
        finally:
            for rel, text in originals.items():
                (root / rel).write_text(text, encoding="utf-8")
        report["edits"].append(
            {
                "changed_files": count,
                "incremental_s": round(incremental_s, 3),
                "full_rebuild_s": round(full_s, 3),
            }
        )
        index = copied
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
