# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Canonical workspace identity for Code Graph sharing.

One realpath is one repo. Remote URL, branch, and commit are not part of the
identity: those change the generation, not which graph entry is shared.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


def canonical_workspace_path(path: str | Path) -> str:
    """Absolute, symlink-resolved workspace path used as graph identity input."""
    raw = os.path.abspath(os.path.expanduser(str(path)))
    try:
        return os.path.realpath(raw)
    except OSError:
        return raw


def workspace_relative_path(root: str | Path, path: str | Path) -> str:
    """Return a repo-relative POSIX path for a tool or filesystem path.

    ``edit_file`` often reports an absolute path. On macOS ``/tmp`` is
    ``/private/tmp``, so stripping a leading slash would store
    ``private/tmp/<repo>/file.py`` instead of ``file.py``.
    """
    root_real = Path(canonical_workspace_path(root))
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text or text in {".", "/"}:
        return ""
    candidates: list[Path] = []
    if text.startswith("/"):
        candidates.append(Path(canonical_workspace_path(text)))
    else:
        # Try the stripped-absolute form first. ``root / "private/tmp/..."``
        # is under the repo as a ghost path and would win otherwise.
        candidates.append(Path(canonical_workspace_path("/" + text)))
        candidates.append(Path(canonical_workspace_path(root_real / text)))
    hits: list[str] = []
    for candidate in candidates:
        try:
            relative = candidate.relative_to(root_real)
        except ValueError:
            continue
        posix = relative.as_posix()
        if posix and posix != ".":
            hits.append(posix)
    for posix in hits:
        if (root_real / posix).exists():
            return posix
    return hits[0] if hits else text.lstrip("/")


def repo_id_for_path(path: str | Path) -> str:
    """Stable hash of the canonical workspace path."""
    canonical = canonical_workspace_path(path)
    normalized = os.path.normcase(canonical)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RepoIdentity:
    """Identity of one on-disk workspace tree."""

    canonical_root: str
    repo_id: str

    @classmethod
    def from_path(cls, path: str | Path) -> "RepoIdentity":
        canonical = canonical_workspace_path(path)
        return cls(canonical_root=canonical, repo_id=repo_id_for_path(canonical))

    def entry_key(self, config_hash: str | None = None) -> str:
        """Live registry key: one workspace, one current graph.

        ``config_hash`` does not fork the entry. A config change rebuilds
        this same graph so every conversation on the path sees it.
        """
        del config_hash
        return self.repo_id
