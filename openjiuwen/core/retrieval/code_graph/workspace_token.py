# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Content-aware workspace tokens and change detection.

Porcelain status text is not enough: a file that is already dirty can change
again without the status line changing. Tokens therefore hash dirty-file
contents (and a bounded mtime walk when git is absent).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.identity import (
    canonical_workspace_path,
    workspace_relative_path,
)
from openjiuwen.core.retrieval.code_graph.models import INDEX_SCHEMA_VERSION, CodeGraphConfig


@dataclass(frozen=True)
class WorkspaceToken:
    """Fingerprint of the workspace that a generation was built from."""

    head: str | None
    dirty_digest: str
    config_hash: str
    schema_version: int
    dirty_paths: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        payload = (
            f"{self.head or ''}|{self.dirty_digest}|{self.config_hash}|"
            f"{self.schema_version}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def incremental_limit(
    indexed_files: int,
    config: CodeGraphConfig,
    *,
    last_full_build_seconds: float | None = None,
) -> int:
    """Max dirty files that still prefer incremental refresh.

    Dense-Python measurements (2026-08-26): incremental of about 70 files
    matches a full rebuild, almost independent of repository size. Repos
    whose last full rebuild finished within ``small_repo_rebuild_seconds``
    always rebuild — both paths are cheap, and refreshing the whole tree
    incrementally is slower. ``indexed_files`` is kept for callers; size
    class is the measured rebuild, not a file-count guess (fat sparse files
    can make a 400-file tree slower than a dense 400-file tree).
    """
    del indexed_files
    small = float(getattr(config, "small_repo_rebuild_seconds", 1.0) or 1.0)
    if last_full_build_seconds is not None and last_full_build_seconds <= small:
        return 0
    return max(0, int(getattr(config, "incremental_max_files", 60) or 0))


def compute_workspace_token(
    repo_root: str | Path,
    config: CodeGraphConfig | None = None,
    *,
    extra_paths: tuple[str, ...] | list[str] = (),
    previous_dirty_paths: tuple[str, ...] | list[str] = (),
) -> WorkspaceToken:
    """Build a token from HEAD plus content hashes of dirty/known paths."""
    cfg = config or CodeGraphConfig()
    root = Path(canonical_workspace_path(repo_root))
    head, dirty_paths = _git_head_and_dirty_paths(root)
    cache_prefix = _cache_rel_prefix(root, cfg)
    tracked = set(_normalized_rels(root, dirty_paths))
    tracked.update(_normalized_rels(root, extra_paths))
    tracked.update(_normalized_rels(root, previous_dirty_paths))
    tracked = {
        path for path in tracked if not _ignored_workspace_rel(path, cfg, cache_prefix)
    }
    ordered = tuple(sorted(tracked))
    if ordered:
        # Known paths are content-addressed so revert-to-same-bytes restores
        # the token. Mixing mtime here would keep a false stale after undo.
        digest = _content_digest(root, ordered)
    elif head is None:
        digest = _mtime_digest(root, cfg)
    else:
        digest = _content_digest(root, ordered)
    return WorkspaceToken(
        head=head,
        dirty_digest=digest,
        config_hash=cfg.config_hash(),
        schema_version=INDEX_SCHEMA_VERSION,
        dirty_paths=ordered,
    )


def hash_workspace_files(repo_root: str | Path, paths: list[str]) -> dict[str, str]:
    """Return repo-relative path -> short content hash for existing files."""
    root = Path(canonical_workspace_path(repo_root))
    hashes: dict[str, str] = {}
    for rel in _normalized_rels(root, paths):
        digest = _file_digest(root / rel)
        if digest is not None:
            hashes[rel] = digest
    return hashes


def detect_changed_paths(
    repo_root: str | Path,
    previous_hashes: dict[str, str],
    *,
    extra_paths: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Paths whose content hash moved, plus extras that are new or gone."""
    root = Path(canonical_workspace_path(repo_root))
    candidates = set(previous_hashes)
    candidates.update(_normalized_rels(root, extra_paths))
    changed: list[str] = []
    for rel in sorted(candidates):
        current = _file_digest(root / rel)
        previous = previous_hashes.get(rel)
        if current != previous:
            changed.append(rel)
    return changed


def head_changed_paths(repo_root: str | Path, old_head: str | None, new_head: str | None) -> list[str]:
    """Files different between two commits. Empty when HEAD did not move."""
    if not old_head or not new_head or old_head == new_head:
        return []
    root = Path(canonical_workspace_path(repo_root))
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", old_head, new_head],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("code_graph token: git diff failed: %s", exc)
        return []
    if completed.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def _git_head_and_dirty_paths(root: Path) -> tuple[str | None, list[str]]:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None, []
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
            ["git", "status", "--porcelain", "-uall"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("code_graph token: git unavailable: %s", exc)
        return None, []
    head_value = head.stdout.strip() if head.returncode == 0 and head.stdout.strip() else None
    paths: list[str] = []
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            path = _porcelain_path(line)
            if path:
                paths.append(path)
    return head_value, paths


def _normalized_rels(root: Path, paths: Iterable[str | Path]) -> list[str]:
    """Repo-relative POSIX paths. Accepts absolute edit_file / realpath forms."""
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        rel = workspace_relative_path(root, raw)
        if rel and rel not in seen:
            seen.add(rel)
            ordered.append(rel)
    return ordered


def _cache_rel_prefix(root: Path, cfg: CodeGraphConfig) -> str | None:
    """Repo-relative cache directory, if the checkpoint lives inside the tree."""
    cache = getattr(cfg, "cache_dir", None)
    if not cache:
        return None
    path = Path(str(cache)).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        rel = Path(canonical_workspace_path(path)).relative_to(root)
    except ValueError:
        return None
    posix = rel.as_posix()
    return posix if posix not in {"", "."} else None


def _ignored_workspace_rel(
    rel: str,
    cfg: CodeGraphConfig,
    cache_prefix: str | None,
) -> bool:
    """Skip tooling / our own cache so they cannot false-stale a generation."""
    posix = str(rel).replace("\\", "/")
    while posix.startswith("./"):
        posix = posix[2:]
    posix = posix.lstrip("/")
    if not posix:
        return True
    if any(cfg.excludes_dir_name(part) or part == ".code_graph_cache" for part in posix.split("/") if part):
        return True
    if cache_prefix and (posix == cache_prefix or posix.startswith(f"{cache_prefix}/")):
        return True
    return False


def _porcelain_path(line: str) -> str:
    if len(line) < 4:
        return ""
    body = line[3:]
    if " -> " in body:
        body = body.split(" -> ", 1)[1]
    return body.strip().strip('"').replace("\\", "/")


def _content_digest(root: Path, paths: tuple[str, ...]) -> str:
    hasher = hashlib.sha256()
    if not paths:
        hasher.update(b"clean")
        return hasher.hexdigest()[:32]
    for rel in paths:
        hasher.update(rel.encode("utf-8", errors="replace"))
        hasher.update(b"\0")
        digest = _file_digest(root / rel)
        hasher.update((digest or "missing").encode("ascii"))
        hasher.update(b"\n")
    return hasher.hexdigest()[:32]


def _file_digest(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:16]


def _mtime_digest(root: Path, cfg: CodeGraphConfig, *, max_files: int = 20000) -> str:
    hasher = hashlib.sha256()
    count = 0
    extra_skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".worktrees", ".code_graph_cache"}
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in extra_skip and not cfg.excludes_dir_name(name)
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
        logger.warning("code_graph token: walk failed: %s", exc)
    return hasher.hexdigest()[:32]
