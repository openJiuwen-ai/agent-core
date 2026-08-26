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
from openjiuwen.agent_teams.schema.team import TeamRole
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
        if self._ensure_real_dir_and_link(binding, root, real_dir):
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
        if self._ensure_real_dir_and_link(binding, root, real_dir):
            self._ref_store.add_ref(
                binding.team_name,
                binding.member_name,
                mode=MEMBER_MODE_DYNAMIC,
                member_workspace_prefix=binding.member_workspace_prefix,
            )
        return root

    @staticmethod
    def _ensure_real_dir_and_link(
        binding: TeamMemberBinding,
        root: Path,
        real_dir: Path,
    ) -> bool:
        """Create the real directory + link, retreating into the team on failure.

        The in-team ``root`` is the member's stable access path either way:
        link succeeds → ``root`` is a link to ``real_dir``; link fails → the
        real directory is created at ``root`` (v3 R2).

        Returns True when ``root`` is a link (real dir external, cross-team
        reuse live); False when the retreat fell back to an in-team real
        directory — the caller must then skip reference counting, since there
        is no shared real dir to count and ``add_ref`` would otherwise
        recreate the team-external directory.
        """
        # Reuse-first: an already-existing link (or a real in-team directory
        # left by a prior retreat) is left as-is.
        if is_dir_link(root):
            return True
        if root.is_dir():
            # Real in-team directory left by a prior retreat.
            return False
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
            # Retreat leaves no team-external residue: the real dir was
            # pre-created for the junction path, so undo it (v3 R2).
            if real_dir.is_dir():
                try:
                    real_dir.rmdir()
                except OSError as clean_exc:
                    team_logger.warning(
                        "could not remove pre-created real dir %s: %s",
                        real_dir,
                        clean_exc,
                    )
            root.mkdir(parents=True, exist_ok=True)
            return False
        return True

    # ── teardown ───────────────────────────────────────────────────────────

    @staticmethod
    def unlink(team_name: str, member_name: str) -> None:
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

    @staticmethod
    def cleanup_team_links(team_name: str) -> None:
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

    def cleanup_team(self, team_name: str) -> None:
        """Detach member links and release dynamic real dirs (block C).

        One-stop cleanup called before any whole-tree ``shutil.rmtree`` of the
        team home: unlink every member workspace link, then release each
        dynamic member's refcount and delete its real directory when the count
        reaches zero. Predefined (shared) and leader (in-team) directories are
        never removed on zero — the store enforces that from ``mode``.

        Predefined ref lists are also released: a disbanded team must drop its
        name from each predefined member's ``.refs.json`` so the list does not
        leak it (``cleanup_team_dynamic_members`` only sees ``<team>#`` dirs).
        The predefined real directory is kept either way (shared-asset
        semantics).
        """
        self.cleanup_team_links(team_name)
        self._ref_store.release_predefined_refs(team_name)
        for member_name in self.cleanup_team_dynamic_members(team_name):
            self.delete_if_zero(team_name, member_name)


def prepare_member_workspace(
    *,
    team_name: str,
    member_name: str,
    role: TeamRole,
    leader_member_name: str | None,
    predefined_members: set[str],
    member_workspace_prefix: bool = True,
) -> str:
    """Ensure the member workspace exists; return the in-team root path.

    Classification is a **role whitelist**: only a ``TEAMMATE`` or
    ``HUMAN_AGENT`` is flattened into a dynamic real directory
    (``.agent_teams/<team>#<m>/``) and linked out of the team tree; a leader
    (by role or name) keeps its real directory in-team; a predefined member
    shares the independent workspace across teams. Every other role — notably
    ``EXTERNAL_CLI`` — stays in-team like a leader, so an external CLI member's
    workspace is never linked out by another member's configure pass. External
    CLI members additionally take the ``member_runtime is not None`` early
    return in ``setup_agent`` and never reach this function; the whitelist is
    the defence-in-depth for any role the binder sees directly.

    The returned path is always ``team_member_workspace_dir`` — when the link
    exists it is transparent; when link creation fails the real directory
    retreats into the team tree (v3 R2). A/B code never notices the link.
    """
    if role == TeamRole.LEADER or member_name == leader_member_name:
        mode = MEMBER_MODE_LEADER
    elif member_name in predefined_members:
        mode = MEMBER_MODE_PREDEFINED
    elif role in (TeamRole.TEAMMATE, TeamRole.HUMAN_AGENT):
        mode = MEMBER_MODE_DYNAMIC
    else:
        # Unknown / non-dynamic role (EXTERNAL_CLI, BRIDGE_AGENT, WORKER, …):
        # stay in-team like a leader — never link another member's workspace
        # out on someone else's configure pass (conservative default).
        mode = MEMBER_MODE_LEADER

    root = MemberWorkspaceBinder().setup(
        TeamMemberBinding(
            team_name=team_name,
            member_name=member_name,
            mode=mode,
            member_workspace_prefix=member_workspace_prefix,
        )
    )
    return str(root)


__all__ = [
    "MemberWorkspaceBinder",
    "TeamMemberBinding",
    "prepare_member_workspace",
]
