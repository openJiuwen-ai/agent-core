# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Repository snapshot used as a Code Graph cache key component.

The cheap fingerprint is content-aware for dirty files so a second edit to an
already-dirty path still invalidates the token. Manager keys no longer include
this snapshot; it lives on the generation / WorkspaceToken instead.

``is_stale`` must use the same inputs as publish: live config (``config_hash``)
and any extra watcher paths. A default-config digest is not comparable to an
overlay-config generation.
"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.workspace_token import compute_workspace_token


def compute_snapshot(
    repo_root: str | Path,
    config: CodeGraphConfig | None = None,
    *,
    extra_paths: tuple[str, ...] | list[str] = (),
    previous_dirty_paths: tuple[str, ...] | list[str] = (),
) -> str:
    """Return a cheap fingerprint of ``repo_root``.

    Uses HEAD plus content hashes of dirty/untracked files. Falls back to a
    bounded mtime walk when git is absent. Pass the same ``config`` the
    generation was built with or the digest will not match.
    """
    return compute_workspace_token(
        repo_root,
        config,
        extra_paths=extra_paths,
        previous_dirty_paths=previous_dirty_paths,
    ).digest
