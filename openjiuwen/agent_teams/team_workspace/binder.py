# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member workspace binder (design-v5, block C).

Creates the on-disk member workspace at spawn time (never at ``build_team``):

- leader:     real directory inside the team, no link
- predefined: ``.agent_teams/members/<member>`` + link ``workspaces/<member>_workspace``
- dynamic:    ``members/<team>#<member>/`` (prefix on) or
              ``members/<member>/`` (prefix off) + link + refcount

Real directories created by the pre-``members/`` layout (directly under
``.agent_teams/``) are migrated into ``members/`` on the next ``setup`` —
best effort under a cross-process lock: the directory is renamed, then every
in-team link pointing at the old location is re-created against the new one
(a predefined dir is shared across teams, so *all* teams' links are fixed,
not just the spawning team's). A rename that fails (e.g. a Windows handle
holds a file inside the dir) logs and keeps the legacy location — probing in
``member_real_dir`` resolves it in place, so correctness never depends on the
migration succeeding.

``setup`` is idempotent — an existing directory or link is left as-is, so
spawn and session recovery converge on the same path. It always returns the
in-team ``team_member_workspace_dir``: when the link exists it is transparent;
when link creation fails it is a real in-team directory (v3 R2, "retreat into
the team tree"). A/B code therefore keeps using ``team_member_workspace_dir``
with zero awareness of the link.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.agent_teams.paths import (
    get_agent_teams_home,
    team_home,
    team_member_workspace_dir,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.skill.file_lock import cross_process_file_lock
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
    members_home,
)
from openjiuwen.agent_teams.team_workspace.ref_store import (
    REFS_FILE_NAME,
    MemberRefStore,
)
from openjiuwen.core.common.logging import team_logger

# Single lock guarding every member-dir migration into ``members/``: two
# sessions setting up the same (or same-named) member concurrently must not
# both try to rename the same legacy dir. The lock is taken on a sidecar next
# to the members/ dir itself.
_MIGRATION_LOCK_NAME = ".members-migration.lock"


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

    @staticmethod
    def _setup_leader(binding: TeamMemberBinding) -> Path:
        root = team_member_workspace_dir(binding.team_name, binding.member_name)
        root.mkdir(parents=True, exist_ok=True)
        # Leaders (and external_cli / other non-dynamic roles that fall back to
        # leader placement) keep their real directory in-team and never link out,
        # so there is no cross-team shared dir to reference-count. Only members
        # whose real dir is linked out of the team tree (dynamic + predefined)
        # carry a ``.refs.json``; leader real dirs are reclaimed by the team-tree
        # rmtree itself.
        return root

    def _setup_predefined(self, binding: TeamMemberBinding) -> Path:
        root = team_member_workspace_dir(binding.team_name, binding.member_name)
        real_dir = self._resolve_and_migrate_real_dir(
            binding,
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
        real_dir = self._resolve_and_migrate_real_dir(
            binding,
            MEMBER_MODE_DYNAMIC,
        )
        if self._ensure_real_dir_and_link(binding, root, real_dir):
            self._ref_store.add_ref(
                binding.team_name,
                binding.member_name,
                mode=MEMBER_MODE_DYNAMIC,
                member_workspace_prefix=binding.member_workspace_prefix,
            )
        return root

    def _resolve_and_migrate_real_dir(
        self,
        binding: TeamMemberBinding,
        mode: str,
    ) -> Path:
        """Resolve the member's real dir, migrating a legacy-root dir into ``members/``.

        ``member_real_dir`` probes ``members/`` first, so when it returns a
        legacy-root path the dir physically sits there and this is the moment
        to move it. The migration runs under the single cross-process
        migration lock with a re-probe inside, so concurrent setups converge
        (the loser finds ``members/`` already populated and uses it). Best
        effort: on any ``OSError`` the legacy location is returned as-is —
        probing keeps it a valid home until a later spawn retries.
        """
        real_dir = member_real_dir(
            binding.team_name,
            binding.member_name,
            mode,
            member_workspace_prefix=binding.member_workspace_prefix,
        )
        if real_dir.parent != get_agent_teams_home():
            # Already under members/ — this is either an existing migrated dir
            # or the fresh-creation target when no probe hit.
            return real_dir
        # member_real_dir returns a legacy-root path only when the dir
        # physically exists there (probe checks is_dir), so migrate it now.
        target = members_home() / real_dir.name
        try:
            with cross_process_file_lock(self._migration_lock_path()):
                # Re-probe under the lock: another process may have migrated
                # this same dir while we were waiting.
                if target.is_dir():
                    return target
                members_home().mkdir(parents=True, exist_ok=True)
                real_dir.rename(target)
                _fix_links_to_real_dir(real_dir, target)
        except OSError as exc:
            team_logger.warning(
                "members/ migration failed for %s; keeping legacy dir %s: %s",
                binding.member_name,
                real_dir,
                exc,
            )
            return real_dir
        team_logger.info(
            "migrated member dir %s -> %s", real_dir.name, target
        )
        return target

    @staticmethod
    def _migration_lock_path() -> Path:
        return members_home().parent / _MIGRATION_LOCK_NAME

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
        """Release every linked-out member real dir, then drop the links.

        Walks the in-team ``<team>/workspaces/<member>_workspace`` entries —
        each is a link to a team-external real dir (dynamic or predefined) —
        resolves the real dir *through the link* (``os.readlink``), drops this
        team from the real dir's ``.refs.json`` ref list, and reclaims the real
        dir only when the ref count reaches zero and the kind is ``dynamic``.
        Predefined real dirs are shared assets and are never removed; leader
        real dirs live in-team (no link) and are reclaimed by the later
        whole-team ``shutil.rmtree`` of ``team_home``.

        Starting from the links (instead of scanning ``.agent_teams/`` by a
        ``<team>#`` name prefix) makes the member→real-dir mapping explicit and
        independent of ``member_workspace_prefix``: a prefix-off dynamic dir
        (``.agent_teams/<member>``) is resolved via its link just like a
        prefix-on one, so neither leaks on team delete. Must run before any
        whole-tree rmtree of ``team_home``: a junction would otherwise be
        descended and delete the shared real-dir contents, and the orphaned
        real dirs + their ``.refs.json`` would otherwise survive the rmtree.
        Fail-soft: an ``OSError`` on any one entry is logged and skipped.
        """
        workspaces_dir = team_home(team_name) / "workspaces"
        if not workspaces_dir.is_dir():
            return
        try:
            entries = sorted(workspaces_dir.iterdir())
        except OSError as exc:
            team_logger.warning("cleanup team scan failed: %s", exc)
            return
        for entry in entries:
            if is_dir_link(entry):
                self._release_linked_member(team_name, entry)
            elif entry.is_dir():
                # A real in-team directory left by a link-creation retreat
                # (v3 R2) — no team-external real dir, no refs. It lives under
                # team_home, so the later whole-tree rmtree would reclaim it,
                # but removing it here keeps cleanup self-contained.
                try:
                    shutil.rmtree(entry)
                except OSError as exc:
                    team_logger.warning("cleanup retreat dir %s failed: %s", entry, exc)

    def _release_linked_member(self, team_name: str, link: Path) -> None:
        """Resolve one member link's real dir, drop the team ref, reclaim dir.

        ``link`` is ``workspaces/<member>_workspace``; the member name is the
        link stem with the ``_workspace`` suffix stripped. The real dir is
        resolved from the link itself (``os.readlink``) so the member's
        ``member_workspace_prefix`` setting never has to be rediscovered.
        """
        member_name = link.name
        if member_name.endswith("_workspace"):
            member_name = member_name[: -len("_workspace")]
        try:
            real_dir = Path(os.readlink(link))
        except OSError as exc:
            # Dangling / unreadable link — nothing to release, just drop it.
            team_logger.warning("cleanup readlink failed for %s: %s", link, exc)
            remove_dir_link(link)
            return
        refs_path = real_dir / REFS_FILE_NAME
        refs = MemberRefStore.load_refs(refs_path)
        if refs is None:
            # No refs file (leader never writes one; or a stale link to a dir
            # that never got a ref). Just drop the link.
            remove_dir_link(link)
            return
        kind, teams = refs
        if team_name not in teams:
            # This team is not in the ref list — nothing to release.
            remove_dir_link(link)
            return
        prefix = kind == MEMBER_MODE_DYNAMIC and f"{team_name}#" in real_dir.name
        try:
            count = self._ref_store.remove_ref(
                team_name,
                member_name,
                mode=kind,
                member_workspace_prefix=prefix,
            )
        except OSError as exc:
            team_logger.warning("cleanup remove_ref failed for %s: %s", link.name, exc)
            remove_dir_link(link)
            return
        # Reclaim the real dir only for dynamic kind on zero; predefined dirs
        # are shared assets and survive (delete_if_zero enforces the same).
        if count == 0 and kind == MEMBER_MODE_DYNAMIC:
            try:
                if real_dir.is_dir():
                    shutil.rmtree(real_dir)
            except OSError as exc:
                team_logger.warning("cleanup rmtree real dir %s failed: %s", real_dir, exc)
        remove_dir_link(link)


def _strip_extended_path_prefix(path: str) -> str:
    """Drop the Windows ``\\\\?\\`` extended-length prefix from a link target.

    ``os.readlink`` on a junction returns the target with the prefix; the
    comparison against a plain path only matches after stripping it.
    """
    prefix = "\\\\?\\"
    return path[len(prefix):] if path.startswith(prefix) else path


def _same_location(left: Path | str, right: Path | str) -> bool:
    """Case-insensitive location equality for link-target comparison."""
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _fix_links_to_real_dir(old_dir: Path, new_dir: Path) -> None:
    """Re-create every in-team link that pointed at ``old_dir``.

    A predefined real dir is shared across teams, so renaming it orphans the
    links of *every* team that references it — not just the team triggering
    the migration. Scan all ``<team>/workspaces/`` entries at the
    ``.agent_teams/`` root, read each link's target, and rebuild the ones
    matching ``old_dir`` against ``new_dir``. Fail-soft: an entry that
    cannot be read or re-linked is logged and skipped.
    """
    root = get_agent_teams_home()
    try:
        team_dirs = sorted(entry for entry in root.iterdir() if entry.is_dir())
    except OSError as exc:
        team_logger.warning("link-fix scan of %s failed: %s", root, exc)
        return
    for team_dir in team_dirs:
        workspaces = team_dir / "workspaces"
        if not workspaces.is_dir():
            continue
        try:
            entries = list(workspaces.iterdir())
        except OSError as exc:
            team_logger.warning("link-fix scan of %s failed: %s", workspaces, exc)
            continue
        for link in entries:
            try:
                target = _strip_extended_path_prefix(os.readlink(link))
            except OSError:
                continue  # not a link, or unreadable
            if not _same_location(target, old_dir):
                continue
            try:
                if remove_dir_link(link):
                    create_dir_link(new_dir, link)
            except OSError as exc:
                team_logger.warning(
                    "link-fix could not rebuild %s -> %s: %s", link, new_dir, exc
                )


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
    (``members/<team>#<m>/``) and linked out of the team tree; a leader
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
