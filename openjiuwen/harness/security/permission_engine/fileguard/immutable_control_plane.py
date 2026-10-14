# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unconditional write protection for host control-plane paths.

Unlike configurable ``file_guard`` rules, this guard is supplied by the host
and remains active when the ordinary permission system is disabled.  It is a
tool-layer boundary; operating-system isolation is still required against
arbitrary native code that path extraction cannot observe.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
    extract_accesses_native,
)
from openjiuwen.harness.security.permission_engine.models import (
    PermissionLevel,
    PermissionResult,
)

_PROJECT_EXECUTABLE_FILES = frozenset({
    "package.json",
    "pyproject.toml",
    "setup.py",
    "makefile",
    "cmakelists.txt",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "pom.xml",
    "build.rs",
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
})


def _canonical(path: Path) -> str:
    try:
        value = path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        value = path.expanduser().absolute()
    return os.path.normcase(os.path.normpath(str(value)))


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _is_project_execution_config(path: str, workspace: str | None) -> bool:
    if workspace is None or not _is_under(path, workspace):
        return False
    relative = os.path.relpath(path, workspace).replace("\\", "/").lower()
    name = relative.rsplit("/", 1)[-1]
    return (
        name in _PROJECT_EXECUTABLE_FILES
        or relative.startswith(".github/workflows/")
        or relative.startswith(".git/hooks/")
        or relative == ".vscode/tasks.json"
    )


class ImmutableControlPlaneGuard:
    """Deny writes/executes below host-provided immutable roots."""

    def __init__(
        self,
        roots: Sequence[Path],
        *,
        workspace_root: Path | None,
        permission_config: Mapping[str, Any] | None = None,
    ) -> None:
        self._roots = tuple(dict.fromkeys(_canonical(Path(root)) for root in roots))
        self._workspace_root = workspace_root
        self._workspace = _canonical(workspace_root) if workspace_root is not None else None
        self._permission_config = permission_config

    @property
    def enabled(self) -> bool:
        return bool(self._roots or self._workspace)

    def evaluate(
        self,
        tool_name: str,
        tool_args: Mapping[str, Any],
    ) -> PermissionResult | None:
        if not self.enabled:
            return None
        workspace = self._workspace_root or Path.cwd()
        accesses = extract_accesses_native(
            tool_name,
            dict(tool_args),
            workspace,
            self._permission_config,
        )
        for path, action, _source in accesses:
            if action not in {"write", "exec"}:
                continue
            canonical = _canonical(path)
            for root in self._roots:
                if _is_under(canonical, root):
                    return PermissionResult(
                        permission=PermissionLevel.DENY,
                        reason=f"Protected control-plane path cannot be modified: {path}",
                        matched_rule="immutable_control_plane",
                        external_paths=[canonical],
                    )
            if _is_project_execution_config(canonical, self._workspace):
                return PermissionResult(
                    permission=PermissionLevel.ASK,
                    reason=f"Project execution configuration requires one-time approval: {path}",
                    matched_rule="immutable_project_execution_config",
                    external_paths=[canonical],
                )
        return None


__all__ = ["ImmutableControlPlaneGuard"]
