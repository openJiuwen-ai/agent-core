# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Path and content-version helpers for PA-14 workspace reads."""

from __future__ import annotations

import hashlib
import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from .access import WorkspaceReadScope

PathDecisionStatus = Literal["forbidden", "unknown"]


class WorkspacePathError(ValueError):
    """Safe path-policy rejection that never contains a host filesystem path."""

    def __init__(self, status: PathDecisionStatus, reason: str = "workspace path is not readable"):
        self.status = status
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ResolvedWorkspacePath:
    """Canonical backend path plus model-safe logical identifiers."""

    path: Path
    relative_path: str
    source_ref: str


def resolve_read_path(scope: WorkspaceReadScope, requested_path: str) -> ResolvedWorkspacePath:
    """Resolve one model-provided path without probing the target resource."""
    if scope.status == "unknown":
        raise WorkspacePathError("unknown", "workspace authorization is unknown")
    if scope.status != "allowed":
        raise WorkspacePathError("forbidden")
    if not isinstance(requested_path, str) or "\x00" in requested_path:
        raise WorkspacePathError("forbidden")

    normalized = requested_path.replace("\\", "/")
    if not normalized:
        normalized = "."
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(requested_path)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or normalized.startswith("/")
    ):
        raise WorkspacePathError("forbidden")

    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if ".." in parts:
        raise WorkspacePathError("forbidden")

    workspace_root = _absolute(scope.workspace_root)
    lexical_candidate = workspace_root.joinpath(*parts) if parts else workspace_root
    private_root = _absolute_optional(scope.private_sessions_root)
    current_root = _absolute_optional(scope.current_session_root)

    if private_root is not None and _is_within(lexical_candidate, private_root) and not (
        current_root is not None and _is_within(lexical_candidate, current_root)
    ):
        raise WorkspacePathError("forbidden")

    allowed_roots = tuple(_absolute(root) for root in scope.allowed_roots)
    allowed = any(_is_within(lexical_candidate, root) for root in allowed_roots)
    if current_root is not None:
        allowed = allowed or _is_within(lexical_candidate, current_root)
    if not allowed:
        raise WorkspacePathError("forbidden")

    candidate = _canonical(lexical_candidate)
    private_root = _canonical_optional(scope.private_sessions_root)
    current_root = _canonical_optional(scope.current_session_root)
    if private_root is not None and _is_within(candidate, private_root) and not (
        current_root is not None and _is_within(candidate, current_root)
    ):
        raise WorkspacePathError("forbidden")
    allowed_roots = tuple(_canonical(root) for root in scope.allowed_roots)
    allowed = any(_is_within(candidate, root) for root in allowed_roots)
    if current_root is not None:
        allowed = allowed or _is_within(candidate, current_root)
    if not allowed:
        raise WorkspacePathError("forbidden")

    relative_path = PurePosixPath(*parts).as_posix() if parts else "."
    source_ref = scope.source_ref.rstrip("/")
    if relative_path != ".":
        source_ref = f"{source_ref}/{relative_path}"
    return ResolvedWorkspacePath(path=candidate, relative_path=relative_path, source_ref=source_ref)


def observe_version(path: str | os.PathLike[str]) -> str | None:
    """Return an opaque metadata version, or None when metadata is unavailable."""
    try:
        metadata = Path(path).stat()
    except OSError:
        return None
    payload = ":".join(
        (
            str(stat_module.S_IFMT(metadata.st_mode)),
            str(metadata.st_size),
            str(metadata.st_mtime_ns),
            str(metadata.st_ctime_ns),
        )
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _absolute_optional(path: Path | None) -> Path | None:
    return _absolute(path) if path is not None else None


def _canonical(path: Path | str) -> Path:
    return Path(os.path.realpath(os.fspath(path)))


def _canonical_optional(path: Path | None) -> Path | None:
    return _canonical(path) if path is not None else None


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
