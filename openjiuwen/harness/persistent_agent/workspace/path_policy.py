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

from .access import WorkspaceReadScope, WorkspaceWriteScope

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

    if scope.host_wide:
        return _resolve_host_path(scope.workspace_root, scope.source_ref, requested_path)

    normalized = requested_path.replace("\\", "/")
    if not normalized:
        normalized = "."
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(requested_path)
    if posix_path.is_absolute() or windows_path.is_absolute() or bool(windows_path.drive) or normalized.startswith("/"):
        raise WorkspacePathError("forbidden")

    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if ".." in parts:
        raise WorkspacePathError("forbidden")

    workspace_root = _absolute(scope.workspace_root)
    lexical_candidate = workspace_root.joinpath(*parts) if parts else workspace_root
    private_root = _absolute_optional(scope.private_sessions_root)
    current_root = _absolute_optional(scope.current_session_root)

    if (
        private_root is not None
        and _is_within(lexical_candidate, private_root)
        and not (current_root is not None and _is_within(lexical_candidate, current_root))
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
    if (
        private_root is not None
        and _is_within(candidate, private_root)
        and not (current_root is not None and _is_within(candidate, current_root))
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


def _resolve_host_path(
    workspace_root: Path,
    source_ref: str,
    requested_path: str,
    *,
    writable: bool = False,
) -> ResolvedWorkspacePath:
    """Resolve full-access paths and leave access checks to the host OS."""
    message = "workspace path is not writable" if writable else "workspace path is not readable"
    windows_path = PureWindowsPath(requested_path)
    if os.name != "nt" and (windows_path.drive or windows_path.is_absolute()):
        raise WorkspacePathError("forbidden", message)
    try:
        requested = Path(requested_path).expanduser()
        if windows_path.is_absolute() and not requested.is_absolute():
            raise WorkspacePathError("forbidden", message)
        candidate = requested if requested.is_absolute() else Path(workspace_root) / requested
        canonical = _canonical(candidate)
    except WorkspacePathError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise WorkspacePathError("forbidden", message) from exc

    logical_path = os.fspath(canonical)
    return ResolvedWorkspacePath(
        path=canonical,
        relative_path=logical_path,
        source_ref=f"{source_ref.rstrip('/')}:{logical_path}",
    )


def resolve_write_path(scope: WorkspaceWriteScope, requested_path: str) -> ResolvedWorkspacePath:
    """Resolve one model path inside the host-granted write scope."""
    if scope.status == "unknown":
        raise WorkspacePathError("unknown", "workspace write authorization is unknown")
    if scope.status != "allowed":
        raise WorkspacePathError("forbidden", "workspace path is not writable")
    if not isinstance(requested_path, str) or not requested_path or "\x00" in requested_path:
        raise WorkspacePathError("forbidden", "workspace path is not writable")
    if any(ord(character) < 32 or ord(character) == 127 for character in requested_path):
        raise WorkspacePathError("forbidden", "workspace path is not writable")

    if scope.host_wide:
        return _resolve_host_path(scope.workspace_root, scope.source_ref, requested_path, writable=True)

    normalized = requested_path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(requested_path)
    if posix_path.is_absolute() or windows_path.is_absolute() or bool(windows_path.drive) or normalized.startswith("/"):
        raise WorkspacePathError("forbidden", "workspace path is not writable")

    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if not parts or ".." in parts or any(":" in part for part in parts):
        raise WorkspacePathError("forbidden", "workspace path is not writable")

    workspace_root = _absolute(scope.workspace_root)
    lexical_candidate = workspace_root.joinpath(*parts)
    private_root = _absolute_optional(scope.private_sessions_root)
    current_root = _absolute_optional(scope.current_session_root)

    if _is_private_session_path(lexical_candidate, private_root, current_root):
        raise WorkspacePathError("forbidden", "workspace path is not writable")
    allowed_roots = tuple(_absolute(root) for root in scope.allowed_write_roots)
    if not _is_allowed(lexical_candidate, allowed_roots, current_root):
        raise WorkspacePathError("forbidden", "workspace path is not writable")

    candidate = _canonical(lexical_candidate)
    canonical_private_root = _canonical_optional(private_root)
    canonical_current_root = _canonical_optional(current_root)
    if _is_private_session_path(candidate, canonical_private_root, canonical_current_root):
        raise WorkspacePathError("forbidden", "workspace path is not writable")
    canonical_allowed_roots = tuple(_canonical(root) for root in allowed_roots)
    if not _is_allowed(candidate, canonical_allowed_roots, canonical_current_root):
        raise WorkspacePathError("forbidden", "workspace path is not writable")

    relative_path = PurePosixPath(*parts).as_posix()
    source_ref = f"{scope.source_ref.rstrip('/')}/{relative_path}"
    return ResolvedWorkspacePath(path=candidate, relative_path=relative_path, source_ref=source_ref)


def _is_allowed(candidate: Path, allowed_roots: tuple[Path, ...], current_root: Path | None) -> bool:
    return any(_is_within(candidate, root) for root in allowed_roots) or (
        current_root is not None and _is_within(candidate, current_root)
    )


def _is_private_session_path(
    candidate: Path,
    private_root: Path | None,
    current_root: Path | None,
) -> bool:
    return (
        private_root is not None
        and _is_within(candidate, private_root)
        and not (current_root is not None and _is_within(candidate, current_root))
    )


__all__ = [
    "ResolvedWorkspacePath",
    "WorkspacePathError",
    "observe_version",
    "resolve_read_path",
    "resolve_write_path",
]
