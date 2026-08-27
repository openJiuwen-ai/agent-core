# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Hard resource budgets checked while a Code Graph index is being built.

Crossing a limit means the repository is not indexed at all — never a
truncated PARTIAL graph. Product stops are repository size (files / source
bytes) and measured process RSS / cache-disk use. Weighted-byte estimates
are for memory LRU only; they do not refuse an index.
"""

from __future__ import annotations

import os
from pathlib import Path

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig


def process_rss_bytes() -> int:
    """Current resident set of this process. 0 if the platform cannot report it."""
    try:
        with open("/proc/self/statm", encoding="utf-8") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess

        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        return int(completed.stdout.strip() or 0) * 1024
    except Exception:  # noqa: BLE001
        return 0


def cache_dir_bytes(cache_dir: str | Path | None) -> int:
    """Actual bytes already used under the checkpoint directory."""
    if not cache_dir:
        return 0
    root = Path(cache_dir)
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def raise_if_resource_limits(
    config: CodeGraphConfig,
    *,
    source_bytes: int = 0,
    rss_bytes: int | None = None,
) -> None:
    """Refuse when measured repo size, RSS, or cache disk is over the cap."""
    if source_bytes > max(1, int(config.max_source_bytes)):
        raise_limit_exceeded("max_source_bytes", source_bytes, config.max_source_bytes)
    rss_cap = max(0, int(config.max_build_rss_mb or 0)) * 1024 * 1024
    if rss_cap:
        rss = process_rss_bytes() if rss_bytes is None else int(rss_bytes)
        if rss >= rss_cap:
            raise_limit_exceeded("max_build_rss_mb", rss, rss_cap)
    if config.cache_dir:
        disk_cap = config.disk_quota_bytes()
        used = cache_dir_bytes(config.cache_dir)
        if used >= disk_cap:
            raise_limit_exceeded("max_cache_size_mb", used, disk_cap)


def raise_limit_exceeded(limit: str, observed: int | str, cap: int | str) -> None:
    """Refuse to index. Callers must not publish a graph after this."""
    raise CodeGraphLimitExceeded(
        (
            f"Code Graph limit exceeded: {limit} is {observed}, cap is {cap}. "
            "This repository is too large to index. The previous graph was "
            "cleared and grep/glob are restored. To index it, raise "
            "max_files, max_source_bytes, max_build_rss_mb, or "
            "max_cache_size_mb, then retry."
        ),
        limit=limit,
        observed=observed,
        cap=cap,
    )


def cancel_requested(cancel: object | None) -> bool:
    """True when a cooperative cancel Event has been set."""
    if cancel is None:
        return False
    checker = getattr(cancel, "is_set", None)
    return bool(checker()) if callable(checker) else False
