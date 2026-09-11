# coding: utf-8
"""Product-path probe: first-build labeling, refresh choice, caps headroom.

Not collected by pytest. Run:

    UV_NO_SYNC=1 uv run python tests/unit_tests/core/retrieval/code_graph/probe_lifecycle_gap.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager, reset_code_graph_manager
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore


def _rss_mb() -> float:
    try:
        out = os.popen(f"ps -o rss= -p {os.getpid()}").read().strip()
        return int(out) / 1024.0
    except Exception:
        return 0.0


def _product_cfg(cache_dir: str) -> CodeGraphConfig:
    return CodeGraphConfig(
        cache_dir=cache_dir,
        max_files=5000,
        max_source_bytes=41_943_040,
        max_build_rss_mb=4096,
        max_cache_size_mb=2048,
        freshness_check_interval_ms=0,
    )


def _touch(path: Path) -> str:
    original = path.read_text(encoding="utf-8")
    path.write_text(original + "\n# probe-touch\n", encoding="utf-8")
    return original


async def _probe_repo(name: str, root: Path, cache: Path, *, pick: str | None) -> dict[str, object]:
    reset_code_graph_manager()
    cfg = _product_cfg(str(cache))
    manager = CodeGraphManager(max_cached_repos=4)
    rss0 = _rss_mb()
    started = time.perf_counter()
    first = await manager.ensure_fresh(root, cfg)
    first_s = time.perf_counter() - started
    entry = manager._peek_entry(root, cfg)
    row: dict[str, object] = {
        "name": name,
        "files": first.index.file_count,
        "symbols": len(first.index.symbols),
        "relations": len(first.index.relations),
        "first_reason": first.reason,
        "first_s": round(first_s, 3),
        "last_full_s": None if entry is None else round(float(entry.last_full_build_seconds or 0), 3),
        "rss_after_first_mb": round(_rss_mb() - rss0, 1),
        "over_files": first.index.file_count > cfg.max_files,
        "over_bytes": False,
    }
    target = (root / pick) if pick else None
    if target is None:
        for rel, _hash in sorted(first.index.file_hashes.items()):
            if rel.endswith(".py"):
                target = root / rel
                break
    if target is None or not target.is_file():
        row["dirty"] = "no python file"
        return row
    original = _touch(target)
    try:
        rel = str(target.relative_to(root)).replace("\\", "/")
        manager.mark_dirty(root, [rel], config=cfg)
        started = time.perf_counter()
        second = await manager.ensure_fresh(root, cfg)
        dirty_s = time.perf_counter() - started
        row["dirty_reason"] = second.reason
        row["dirty_s"] = round(dirty_s, 3)
        row["dirty_path"] = rel
    finally:
        target.write_text(original, encoding="utf-8")

    reset_code_graph_manager()
    restarted = CodeGraphManager(max_cached_repos=4)
    loaded = await restarted.ensure_fresh(root, cfg)
    restored = restarted._peek_entry(root, cfg)
    row["restart_reason"] = loaded.reason
    row["restart_last_full_s"] = (
        None if restored is None else round(float(restored.last_full_build_seconds or 0), 3)
    )
    pickle_bytes = 0
    if cache.is_dir():
        pickle_bytes = DiskIndexStore(cache).used_bytes()
    row["cache_mb"] = round(pickle_bytes / (1024 * 1024), 2)
    row["files_headroom"] = cfg.max_files - int(first.index.file_count)
    return row


async def _probe_tiny(cache_root: Path) -> dict[str, object]:
    tiny = cache_root / "tiny"
    tiny.mkdir(parents=True)
    (tiny / "app.py").write_text(
        "class PolicyNet:\n    def run(self):\n        return 1\n\ndef caller():\n    PolicyNet().run()\n",
        encoding="utf-8",
    )
    return await _probe_repo("tiny", tiny, cache_root / "tiny-cache", pick="app.py")


async def main() -> None:
    here = Path(__file__).resolve()
    agent_core = here.parents[5]
    jiuwen = agent_core.parent / "jiuwenswarm"
    out_dir = jiuwen / "docs" / "ai" / "01-dev" / "03-index-management" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        rows = [await _probe_tiny(tmp / "tiny-root")]
        if agent_core.is_dir():
            rows.append(await _probe_repo("agent-core", agent_core, tmp / "ac-cache", pick=None))
        if jiuwen.is_dir():
            rows.append(await _probe_repo("jiuwenswarm", jiuwen, tmp / "jw-cache", pick=None))
    report = {
        "ok": all(row.get("first_reason") == "full" for row in rows),
        "rows": rows,
    }
    out = out_dir / "lifecycle_gap_20260827.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
