# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Worktree coordination for member optimization actions.

Per feat_009 Section 3.7 and design.md Section 4.9.
Allocates isolated worktrees for parallel action execution:
- Integration worktree: wt/{role_hash}/i
- Action worktree: wt/{role_hash}/a/{n}/{action_hash}
All paths are validated to be relative to the role worktree root.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from openjiuwen.rsi.harness_rsi.member_optimizer.schema import MemberOptimizationAction

MEMBER_WORKTREES_DIR_NAME = "wt"


def _validate_worktree_path(path: str, role_worktree_root: Path) -> bool:
    """Validate that a path is within the role worktree root.

    Returns True if the path is:
    - Relative (not absolute)
    - Does not traverse above role_worktree_root
    - Does not reference shared artifact directories
    """
    if not path:
        return False
    abs_path = (role_worktree_root / path).resolve()
    try:
        abs_path.relative_to(role_worktree_root.resolve())
    except ValueError:
        return False
    forbidden = {"current_harnesses", "current_harness_refs.yaml", ".publish_tmp"}
    if any(segment in forbidden for segment in Path(path).parts):
        return False
    return True


def _action_worktree_name(action_id: str) -> str:
    return hashlib.sha1(action_id.encode("utf-8")).hexdigest()[:8]


def _role_worktree_name(role: str) -> str:
    return hashlib.sha1(role.encode("utf-8")).hexdigest()[:8]


def role_worktree_path(worktrees_dir: str | Path, role: str) -> Path:
    """Return the short per-role worktree root for a role."""
    return Path(worktrees_dir).expanduser().resolve() / _role_worktree_name(role)


def legacy_role_worktree_path(worktrees_dir: str | Path, role: str) -> Path:
    """Return the old readable per-role worktree root for legacy artifacts/tests."""
    return Path(worktrees_dir).expanduser().resolve() / role


def integration_worktree_path(worktrees_dir: str | Path, role: str) -> Path:
    """Return the short integration worktree path for a role."""
    return role_worktree_path(worktrees_dir, role) / "i"


def resolve_integration_worktree_path(worktrees_dir: str | Path, role: str) -> Path:
    """Return new integration path, falling back to legacy path if only that exists."""
    short_path = integration_worktree_path(worktrees_dir, role)
    if short_path.exists():
        return short_path
    legacy_path = legacy_role_worktree_path(worktrees_dir, role) / "integration"
    if legacy_path.exists():
        return legacy_path
    return short_path


def _write_role_mapping(root: Path, role: str, role_dir_name: str) -> None:
    mapping_path = root / "roles.yaml"
    payload = {"roles": {}}
    if mapping_path.is_file():
        try:
            loaded = yaml.safe_load(mapping_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {"roles": {}}
    roles = payload.setdefault("roles", {})
    if isinstance(roles, dict):
        roles[role] = role_dir_name
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )


if TYPE_CHECKING:
    from openjiuwen.harness.tools.worktree import WorktreeConfig


class MemberWorktreeCoordinator:
    """Allocate isolated workspaces for parallel member optimization actions.

    Directory scheme per feat_009 design.md Section 4.9:
    - worktrees/{role}/integration/        — merged state after each wave
    - worktrees/{role}/waves/wave_N/      — per-wave directory
    - worktrees/{role}/waves/wave_N/{action_id}/  — isolated action worktree

    Action workers write only to their isolated action worktree.
    Merge workers write only to the integration worktree.
    No worker writes directly to current_harnesses or current_harness_refs.yaml.
    """

    def __init__(self, config: "WorktreeConfig | None" = None) -> None:
        self._config = config

    @staticmethod
    def prepare_integration_worktree(
        role: str,
        harness_ref_path: str,
        worktrees_dir: str,
    ) -> Path:
        """Prepare a role's integration worktree by copying the source harness.

        Args:
            role: The role name (used in path construction).
            harness_ref_path: Path to the source Expert Harness.
            worktrees_dir: Root worktrees directory.

        Returns:
            Path to the integration worktree directory.
        """
        root = Path(worktrees_dir).expanduser().resolve()
        role_root = role_worktree_path(root, role)
        integration_dir = role_root / "i"
        integration_dir.mkdir(parents=True, exist_ok=True)
        _write_role_mapping(root, role, role_root.name)

        source = Path(harness_ref_path).expanduser().resolve() if harness_ref_path else None
        if source is not None and source.exists():
            if source.is_dir():
                for item in source.iterdir():
                    dest = integration_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
            elif source.is_file():
                shutil.copy2(source, integration_dir / source.name)

        return integration_dir

    @staticmethod
    def prepare_action_worktree(
        action: MemberOptimizationAction,
        integration_worktree: Path,
        worktrees_dir: str,
        wave_index: int,
    ) -> Path:
        """Prepare an isolated action worktree by snapshotting the integration directory.

        Args:
            action: The action being executed.
            integration_worktree: Path to the role's integration worktree snapshot.
            worktrees_dir: Root worktrees directory.
            wave_index: Global wave index.

        Returns:
            Path to the isolated action worktree.

        Raises:
            ValueError: if target_path is outside the role worktree.
        """
        role_worktree_root = role_worktree_path(worktrees_dir, action.role)

        if action.target_path:
            if not _validate_worktree_path(action.target_path, role_worktree_root):
                raise ValueError(
                    f"target_path '{action.target_path}' is outside role worktree or references forbidden paths"
                )

        action_worktree = role_worktree_root / "a" / f"{wave_index:x}" / _action_worktree_name(action.action_id)
        action_worktree.mkdir(parents=True, exist_ok=True)

        if integration_worktree.exists():
            for item in integration_worktree.iterdir():
                dest = action_worktree / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

        return action_worktree


__all__ = [
    "MEMBER_WORKTREES_DIR_NAME",
    "MemberWorktreeCoordinator",
    "integration_worktree_path",
    "legacy_role_worktree_path",
    "resolve_integration_worktree_path",
    "role_worktree_path",
]
