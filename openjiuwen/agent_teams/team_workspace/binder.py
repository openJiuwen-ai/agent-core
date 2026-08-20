# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member workspace binder (design-v5, block C).

Creates the on-disk member workspace at spawn time (never at ``build_team``):

- leader:     real directory inside the team, no link
- predefined: ``.agent_teams/<member>`` + link ``workspaces/<member>_workspace``
- dynamic:    ``.agent_teams/<team>#<member>/`` (prefix on) or
              ``.agent_teams/<member>/`` (prefix off) + link + refcount

``setup`` is idempotent — an existing directory or link is left as-is, so
spawn and session recovery converge on the same path. It always returns the
in-team ``team_member_workspace_dir``: when the link exists it is transparent;
when link creation fails it is a real in-team directory (v3 R2, "retreat into
the team tree"). A/B code therefore keeps using ``team_member_workspace_dir``
with zero awareness of the link.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openjiuwen.agent_teams.paths import team_home, team_member_workspace_dir
from openjiuwen.agent_teams.team_workspace.dir_links import (
    create_dir_link,
    is_dir_link,
    remove_dir_link,
)
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_LEADER,
    MEMBER_MODE_PREDEFINED,
    member_real_dir,
)
from openjiuwen.agent_teams.team_workspace.ref_store import MemberRefStore
from openjiuwen.core.common.logging import team_logger


@dataclass(frozen=True)
class TeamMemberBinding:
    """Identity + placement of one team member workspace."""

    team_name: str
    member_name: str
    mode: str = MEMBER_MODE_DYNAMIC
    member_workspace_prefix: bool = True
    """Dynamic-only: True → ``team#member`` isolation; False → ``member``."""


class MemberWorkspaceBinder:
    """Create / unlink / release member workspaces and their links."""

    def __init__(self, ref_store: MemberRefStore | None = None) -> None:
        self._ref_store = ref_store or MemberRefStore()

    # ── setup ──────────────────────────────────────────────────────────────

    def setup(self, binding: TeamMemberBinding) -> Path:
        """Ensure the member workspace exists; return the in-team root.

        Idempotent: existing real directories and links are reused. Dynamic
        and predefined members get a reference-count entry; leader directories
        are team-owned and never deleted on zero.
        """
        if binding.mode == MEMBER_MODE_LEADER:
            return self._setup_leader(binding)
        if binding.mode == MEMBER_MODE_PREDEFINED:
            return self._setup_predefined(binding)
        return self._setup_dynamic(binding)

    def _setup_leader(self, binding: TeamMemberBinding) -> Path:
        root = team_member_workspace_dir(binding.team_name, binding.member_name)
        root.mkdir(parents=True, exist_ok=True)
        # Leaders are team-owned assets; count but never delete on zero.
        self._ref_store.add_ref(
            binding.team_name,
            binding.member_name,
            mode=MEMBER_MODE_LEADER,
        )
        return root

    def _setup_predefined(self, binding: TeamMemberBinding) -> Path:
        root = team_member_workspace_dir(binding.team_name, binding.member_name)
        real_dir = member_real_dir(
            binding.team_name,
            binding.member_name,
            MEMBER_MODE_PREDEFINED,
        )
        self._ensure_real_dir_and_link(binding, root, real_dir)
        self._ref_store.add_ref(
            binding.team_name,
            binding.member_name,
            mode=MEMBER_MODE_PREDEFINED,
        )
        return root

    def _setup_dynamic(self, binding: TeamMemberBinding) -> Path:
        root = team_member_workspace_dir(binding.team_name, binding.member_name)
        real_dir = member_real_dir(
            binding.team_name,
            binding.member_name,
            MEMBER_MODE_DYNAMIC,
            member_workspace_prefix=binding.member_workspace_prefix,
        )
        self._ensure_real_dir_and_link(binding, root, real_dir)
        self._ref_store.add_ref(
            binding.team_name,
            binding.member_name,
            mode=MEMBER_MODE_DYNAMIC,
            member_workspace_prefix=binding.member_workspace_prefix,
        )
        return root

    def _ensure_real_dir_and_link(
        self,
        binding: TeamMemberBinding,
        root: Path,
        real_dir: Path,
    ) -> None:
        """Create the real directory + link, retreating into the team on failure.

        The in-team ``root`` is the member's stable access path either way:
        link succeeds → ``root`` is a link to ``real_dir``; link fails → the
        real directory is created at ``root`` (v3 R2).
        """
        # Reuse-first: an already-existing link (or a real in-team directory
        # left by a prior retreat) is left as-is.
        if is_dir_link(root) or root.is_dir():
            return
        real_dir.parent.mkdir(parents=True, exist_ok=True)
        real_dir.mkdir(parents=True, exist_ok=True)
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            create_dir_link(real_dir, root)
        except OSError as exc:
            team_logger.warning(
                "link creation failed for %s/%s; retreating into team tree: %s",
                binding.team_name,
                binding.member_name,
                exc,
            )
            root.mkdir(parents=True, exist_ok=True)
            return

    # ── teardown ───────────────────────────────────────────────────────────

    def unlink(self, team_name: str, member_name: str) -> None:
        """Remove the link only; the real directory is untouched."""
        remove_dir_link(team_member_workspace_dir(team_name, member_name))

    def release(
        self,
        team_name: str,
        member_name: str,
        *,
        mode: str = MEMBER_MODE_DYNAMIC,
        member_workspace_prefix: bool = True,
    ) -> bool:
        """Unlink the link and decrement the refcount.

        ``mode`` locates the real directory's ``.refs.json`` (predefined → the
        shared independent workspace). Returns True when the count reached zero
        (caller may then ``delete_if_zero`` after confirming no active writer).
        """
        self.unlink(team_name, member_name)
        count = self._ref_store.remove_ref(
            team_name,
            member_name,
            mode=mode,
            member_workspace_prefix=member_workspace_prefix,
        )
        return count == 0

    def cleanup_team_dynamic_members(self, team_name: str) -> list[str]:
        """Batch-release every dynamic member of a team.

        Returns member names whose refcount hit zero, ready for
        ``delete_if_zero`` by the caller.
        """
        return self._ref_store.cleanup_team_dynamic_members(team_name)

    def cleanup_team_links(self, team_name: str) -> None:
        """Remove every link under ``<team>/workspaces/``; never touch targets.

        Call before any whole-tree ``shutil.rmtree`` of the team home — a
        junction would otherwise be descended and delete the target contents.
        """
        workspaces_dir = team_home(team_name) / "workspaces"
        if not workspaces_dir.is_dir():
            return
        try:
            entries = sorted(workspaces_dir.iterdir())
        except OSError as exc:
            team_logger.warning("cleanup team links scan failed: %s", exc)
            return
        for entry in entries:
            if is_dir_link(entry):
                remove_dir_link(entry)

    def delete_if_zero(
        self,
        team_name: str,
        member_name: str,
        *,
        mode: str = MEMBER_MODE_DYNAMIC,
        member_workspace_prefix: bool = True,
    ) -> bool:
        """Remove the real directory iff refcount is zero (see store).

        Predefined / leader directories are never removed on zero (shared
        assets) — the store enforces that from ``mode``.
        """
        return self._ref_store.delete_if_zero(
            team_name,
            member_name,
            mode=mode,
            member_workspace_prefix=member_workspace_prefix,
        )


__all__ = ["MemberWorkspaceBinder", "TeamMemberBinding"]
