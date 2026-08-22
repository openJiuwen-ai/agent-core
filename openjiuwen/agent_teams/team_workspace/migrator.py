# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Legacy → design-v5 layout migration (design-v5, block C).

The legacy layout kept every member's real directory inside the team tree at
``<team>/workspaces/<member>_workspace/``. design-v5 flattens the real
directories into ``.agent_teams/`` (dynamic) or the independent workspace
(predefined) and exposes them through links. The migrator renames real
directories and creates links; it never touches the DB.

The leader is never flattened: its real directory already lives at the link
position (``team_member_workspace_dir``) and stays there — a leader is part of
the team, not a reusable workspace.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.team_workspace.dir_links import (
    create_dir_link,
    is_dir_link,
)
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_PREDEFINED,
    member_real_dir,
)
from openjiuwen.core.common.logging import team_logger


class TeamWorkspaceMigrator:
    """Migrate a team's member workspaces from the legacy nested layout.

    For each real directory under ``<team>/workspaces/`` (skipping links and
    dotfiles) the member is classified and moved to its design-v5 real
    directory, then a link is created at the legacy link position. Idempotent:
    links are skipped. The leader is never moved or linked.
    """

    def migrate(
        self,
        team_name: str,
        *,
        leader_member_name: str | None = None,
        predefined_members: set[str] | None = None,
        persistent_members: set[str] | None = None,
        member_workspace_prefix: bool = True,
    ) -> bool:
        """Run the filesystem migration for one team.

        Args:
            leader_member_name: The team's leader member name. Its legacy
                directory is left in place — the leader stays in-team and is
                never linked out.
            predefined_members: Names of predefined (shared) members; their
                directories move to the independent workspace. Members not in
                this set are treated as dynamic.
            persistent_members: Persistent roster snapshot (leader +
                predefined + DB ``team_member`` rows). Real directories **not**
                in this set are worker leftovers (ephemeral swarmflow
                executors never enter the roster) — they are skipped. ``None``
                falls back to classifying everything non-leader/non-predefined
                as dynamic.
            member_workspace_prefix: Passed through to the dynamic real-dir
                formula; must match the value the binder will use, otherwise
                migrated content and the reference count land in different
                directories.

        Returns True when any directory was moved. Never touches the DB.
        """
        workspaces_dir = team_home(team_name) / "workspaces"
        if not workspaces_dir.is_dir():
            return False
        moved = False
        try:
            entries = sorted(workspaces_dir.iterdir())
        except OSError as exc:
            team_logger.warning("migrate scan %s failed: %s", workspaces_dir, exc)
            return False
        predefined = predefined_members or set()
        persistent = persistent_members or set()
        for entry in entries:
            if entry.name.startswith(".") or is_dir_link(entry):
                continue
            if not entry.is_dir():
                continue
            member_name = self._member_name_from_legacy_dir(entry.name)
            if member_name is None:
                team_logger.warning("migrate: cannot parse member dir %s", entry.name)
                continue
            if member_name == leader_member_name:
                # Leader real dir already lives at the in-team link position
                # (``workspaces/<member>_workspace``) — nothing to flatten.
                continue
            if persistent and member_name not in persistent:
                team_logger.warning(
                    "migrate: skipping non-roster dir %s (worker leftover?)", entry.name
                )
                continue
            mode = MEMBER_MODE_PREDEFINED if member_name in predefined else MEMBER_MODE_DYNAMIC
            target = member_real_dir(
                team_name,
                member_name,
                mode,
                member_workspace_prefix=member_workspace_prefix,
            )
            if target == entry:
                continue
            if target.exists():
                # Shared real dir already live (a prior migrate, or another
                # team's binder setup). The legacy entry is stale residue:
                # drop it and link to the existing dir — a plain rename would
                # raise ``Directory not empty`` / ``PermissionError`` on an
                # occupied target, or swap an empty shared dir out from under
                # a live link.
                try:
                    shutil.rmtree(entry)
                except OSError as exc:
                    team_logger.warning(
                        "migrate: dropping stale legacy dir %s failed: %s", entry, exc
                    )
                    continue
                if not self._link(target, team_name, member_name):
                    # Link failed — retreat into the team tree (v3 R2): the
                    # shared dir stays untouched, this team gets its own dir.
                    entry.mkdir(parents=True, exist_ok=True)
                    continue
                moved = True
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            entry.rename(target)
            if not self._link(target, team_name, member_name):
                # Link creation failed — roll the directory back so the team
                # tree keeps its real directory (retreat-into-team semantics).
                try:
                    target.rename(entry)
                except OSError as exc:
                    team_logger.warning("migrate rollback %s failed: %s", target, exc)
                continue
            moved = True
        return moved

    @staticmethod
    def _link(real_dir: Path, team_name: str, member_name: str) -> bool:
        """Create the in-team link pointing at ``real_dir``; False on failure."""
        link = team_home(team_name) / "workspaces" / f"{member_name}_workspace"
        try:
            create_dir_link(real_dir, link)
        except OSError as exc:
            team_logger.warning("migrate link %s failed: %s", link, exc)
            return False
        return True

    @staticmethod
    def _member_name_from_legacy_dir(dir_name: str) -> str | None:
        """Strip the legacy ``_workspace`` suffix (``worker-a_workspace``)."""
        if dir_name.endswith("_workspace"):
            return dir_name[: -len("_workspace")]
        return dir_name if not dir_name.startswith(".") else None


__all__ = ["TeamWorkspaceMigrator"]
