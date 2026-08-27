# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""One workspace watcher per canonical path.

This is a correctness accelerator for IDE / Git edits that never went through
Agent write tools. Query-time tokens remain the source of truth.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.workspace_token import compute_workspace_token

_tasks: dict[str, asyncio.Task[None]] = {}
_last_digest: dict[str, str] = {}


def start_workspace_watch(repo_root: str | Path, config: CodeGraphConfig | None = None) -> None:
    """Start a process-wide poller for ``repo_root``. Safe to call repeatedly."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    cfg = config or CodeGraphConfig()
    identity = RepoIdentity.from_path(repo_root)
    existing = _tasks.get(identity.repo_id)
    if existing is not None and not existing.done():
        return
    interval = max(0.5, float(getattr(cfg, "watch_interval_seconds", 2.0) or 2.0))
    _tasks[identity.repo_id] = loop.create_task(
        _watch_loop(identity.canonical_root, cfg, identity.repo_id, interval),
        name=f"code-graph-watch-{identity.repo_id[:8]}",
    )


def stop_workspace_watch(repo_root: str | Path | None = None) -> None:
    """Cancel one watcher, or all watchers when ``repo_root`` is omitted."""
    if repo_root is None:
        keys = list(_tasks)
    else:
        keys = [RepoIdentity.from_path(repo_root).repo_id]
    for key in keys:
        task = _tasks.pop(key, None)
        _last_digest.pop(key, None)
        if task is not None:
            task.cancel()


async def _watch_loop(repo_root: str, cfg: CodeGraphConfig, repo_id: str, interval: float) -> None:
    from openjiuwen.core.retrieval.code_graph.manager import get_code_graph_manager

    while True:
        try:
            await asyncio.sleep(interval)
            token = compute_workspace_token(repo_root, cfg)
            previous = _last_digest.get(repo_id)
            _last_digest[repo_id] = token.digest
            if previous is None or previous == token.digest:
                continue
            manager = get_code_graph_manager(cfg)
            async with manager._lock:
                manager.reclaim()
            if token.dirty_paths:
                manager.mark_dirty(repo_root, list(token.dirty_paths), source="watch", config=cfg)
            else:
                manager.mark_dirty_unknown(repo_root, reason="watch", config=cfg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — watcher must not kill the agent
            logger.warning("code_graph watch failed for %s: %s", repo_root, exc)
