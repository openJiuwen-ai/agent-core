# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Repository snapshot used as a Code Graph cache key component."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger


def compute_snapshot(repo_root: str | Path) -> str:
    """Return a cheap fingerprint of ``repo_root``.

    Prefers ``git HEAD`` plus porcelain status so uncommitted edits invalidate
    the cache. Falls back to a bounded walk of file mtimes when git is absent.
    """
    root = Path(repo_root).resolve()
    git_dir = root / ".git"
    if git_dir.exists():
        digest = _git_snapshot(root)
        if digest:
            return digest
    return _mtime_snapshot(root)


def _git_snapshot(root: Path) -> str | None:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("code_graph snapshot: git unavailable: %s", exc)
        return None
    if head.returncode != 0:
        # Empty repo / not a git work tree.
        payload = f"nogit-head:{status.stdout}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    payload = f"{head.stdout.strip()}\n{status.stdout}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _mtime_snapshot(root: Path, *, max_files: int = 20000) -> str:
    hasher = hashlib.sha256()
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                name for name in dirnames if name not in {".git", "node_modules", "__pycache__", ".venv", "venv"}
            )
            rel_dir = os.path.relpath(dirpath, root)
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                try:
                    stat = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                hasher.update(rel.encode("utf-8", errors="replace"))
                hasher.update(b"\0")
                hasher.update(str(int(stat.st_mtime_ns)).encode("ascii"))
                hasher.update(b"\0")
                hasher.update(str(stat.st_size).encode("ascii"))
                hasher.update(b"\n")
                count += 1
                if count >= max_files:
                    hasher.update(b"#truncated")
                    return hasher.hexdigest()[:32]
    except OSError as exc:
        logger.warning("code_graph snapshot: walk failed: %s", exc)
    return hasher.hexdigest()[:32]
