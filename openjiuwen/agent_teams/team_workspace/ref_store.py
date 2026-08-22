# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reference tracking for member workspaces (design-v5, block C).

Every member real directory carries a ``.refs.json`` marking its ``kind``
(``dynamic`` / ``predefined`` / ``leader``) and the list of **team names**
that reference it::

    {"kind": "dynamic", "teams": ["teamA", "teamB"]}

The list is the source of truth — the reference count is its length.

- ``add_ref`` is idempotent **per team**: re-spawning the same member in the
  same team never adds a duplicate entry; a second team reusing the same
  shared (predefined) directory appends its name.
- ``remove_ref`` drops that team's name; when the list empties the
  ``.refs.json`` file itself is removed (atomic, cheap) and the directory
  becomes eligible for deletion (dynamic kind only).
- Only dynamic directories are ever deleted on zero; predefined / leader
  directories are shared assets and keep their directory when the list
  empties.

Legacy ``{"count": n}`` files (from earlier experiments) are treated as empty
on read and rewritten with the teams-list shape on the next ``add_ref``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from openjiuwen.agent_teams.paths import get_agent_teams_home
from openjiuwen.agent_teams.skill.file_lock import cross_process_file_lock
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_PREDEFINED,
    member_real_dir,
)
from openjiuwen.core.common.logging import team_logger

_REFS_FILE = ".refs.json"


class MemberRefStore:
    """Persist per-member reference team lists in ``.refs.json``."""

    def add_ref(
        self,
        team_name: str,
        member_name: str,
        *,
        mode: str = MEMBER_MODE_DYNAMIC,
        member_workspace_prefix: bool = True,
    ) -> int:
        """Record one team reference; return the resulting count.

        The read-modify-write of ``.refs.json`` runs under a cross-process
        file lock (same primitive as ``skills-visibility.json`` writers) so
        two teams adding references concurrently never lose an entry.
        """
        refs_path = self._refs_path(
            team_name,
            member_name,
            mode=mode,
            member_workspace_prefix=member_workspace_prefix,
        )
        with cross_process_file_lock(refs_path):
            data = self._read(refs_path)
            if data is None:
                refs_path.parent.mkdir(parents=True, exist_ok=True)
                self._write(refs_path, {"kind": mode, "teams": [team_name]})
                return 1
            teams = self._teams(data)
            if team_name not in teams:
                teams.append(team_name)
                data["kind"] = data.get("kind") or mode
                data["teams"] = teams
                self._write(refs_path, data)
            return len(teams)

    def remove_ref(
        self,
        team_name: str,
        member_name: str,
        *,
        mode: str = MEMBER_MODE_DYNAMIC,
        member_workspace_prefix: bool = True,
    ) -> int:
        """Drop one team reference; return the resulting count.

        At zero only ``.refs.json`` is removed — the directory stays until the
        caller confirms no active writer and calls ``delete_if_zero``.
        """
        refs_path = self._refs_path(
            team_name,
            member_name,
            mode=mode,
            member_workspace_prefix=member_workspace_prefix,
        )
        with cross_process_file_lock(refs_path):
            data = self._read(refs_path)
            if data is None:
                return 0
            teams = self._teams(data)
            if team_name in teams:
                teams.remove(team_name)
            if not teams:
                refs_path.unlink(missing_ok=True)
                return 0
            data["teams"] = teams
            self._write(refs_path, data)
            return len(teams)

    def get_ref_count(
        self,
        team_name: str,
        member_name: str,
        *,
        mode: str = MEMBER_MODE_DYNAMIC,
        member_workspace_prefix: bool = True,
    ) -> int:
        """Return the number of referencing teams; 0 when no refs file exists."""
        data = self._read(
            self._refs_path(
                team_name,
                member_name,
                mode=mode,
                member_workspace_prefix=member_workspace_prefix,
            )
        )
        return len(self._teams(data)) if data is not None else 0

    def get_ref_teams(
        self,
        team_name: str,
        member_name: str,
        *,
        mode: str = MEMBER_MODE_DYNAMIC,
        member_workspace_prefix: bool = True,
    ) -> list[str]:
        """Return the referencing team names; empty when no refs file exists."""
        data = self._read(
            self._refs_path(
                team_name,
                member_name,
                mode=mode,
                member_workspace_prefix=member_workspace_prefix,
            )
        )
        return list(self._teams(data)) if data is not None else []

    def delete_if_zero(
        self,
        team_name: str,
        member_name: str,
        *,
        mode: str = MEMBER_MODE_DYNAMIC,
        member_workspace_prefix: bool = True,
    ) -> bool:
        """Remove the real directory iff no team references remain.

        Only dynamic directories are ever removed on zero; predefined / leader
        directories keep their directory (shared-asset semantics). Callers must
        confirm no active writer remains before invoking this.
        """
        if mode != MEMBER_MODE_DYNAMIC:
            return False
        member_dir = member_real_dir(
            team_name,
            member_name,
            mode,
            member_workspace_prefix=member_workspace_prefix,
        )
        if self.get_ref_count(
            team_name,
            member_name,
            mode=mode,
            member_workspace_prefix=member_workspace_prefix,
        ) > 0:
            return False
        if not member_dir.is_dir():
            return False
        try:
            shutil.rmtree(member_dir)
        except OSError as exc:
            team_logger.warning("delete_if_zero: remove %s failed: %s", member_dir, exc)
            return False
        return True

    def cleanup_team_dynamic_members(self, team_name: str) -> list[str]:
        """Release every ``<team>#`` prefixed dynamic directory.

        Returns the member names whose reference list reached zero (candidates
        for ``delete_if_zero``). Fail-soft: an ``OSError`` logs a warning and
        is skipped.
        """
        freed: list[str] = []
        base = get_agent_teams_home()
        prefix = f"{team_name}#"
        try:
            entries = sorted(base.iterdir())
        except OSError as exc:
            team_logger.warning("cleanup dynamic members scan failed: %s", exc)
            return freed
        for entry in entries:
            if not entry.is_dir() or not entry.name.startswith(prefix):
                continue
            member_name = entry.name[len(prefix):]
            try:
                count = self.remove_ref(team_name, member_name, mode=MEMBER_MODE_DYNAMIC)
            except OSError as exc:
                team_logger.warning("cleanup remove_ref failed for %s: %s", entry.name, exc)
                continue
            if count == 0:
                freed.append(member_name)
        return freed

    def release_predefined_refs(self, team_name: str) -> list[str]:
        """Drop ``team_name`` from every predefined member's ref list.

        Predefined (shared) directories live at ``.agent_teams/<member>`` (no
        ``<team>#`` prefix), so ``cleanup_team_dynamic_members`` cannot see
        them. This is the missing counterpart: for each predefined real dir
        whose ``.refs.json`` references ``team_name``, drop that team so the
        ref list does not leak a disbanded team. The real directory is never
        removed — predefined dirs are shared assets
        (``delete_if_zero`` returns False for non-dynamic modes).
        """
        released: list[str] = []
        base = get_agent_teams_home()
        try:
            entries = sorted(base.iterdir())
        except OSError as exc:
            team_logger.warning("release predefined refs scan failed: %s", exc)
            return released
        for entry in entries:
            # Dynamic dirs carry the ``<team>#`` prefix; team dirs hold a
            # ``team-workspace`` subfolder, not a member ``.refs.json``. Only
            # a flat predefined dir with a ``.refs.json`` of kind predefined
            # is a candidate here.
            if not entry.is_dir() or "#" in entry.name:
                continue
            refs_path = entry / _REFS_FILE
            data = self._read(refs_path)
            if data is None or data.get("kind") != MEMBER_MODE_PREDEFINED:
                continue
            if team_name not in self._teams(data):
                continue
            try:
                self.remove_ref(team_name, entry.name, mode=MEMBER_MODE_PREDEFINED)
            except OSError as exc:
                team_logger.warning("release predefined ref failed for %s: %s", entry.name, exc)
                continue
            released.append(entry.name)
        return released

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _refs_path(
        team_name: str,
        member_name: str,
        *,
        mode: str,
        member_workspace_prefix: bool,
    ) -> Path:
        return member_real_dir(
            team_name,
            member_name,
            mode,
            member_workspace_prefix=member_workspace_prefix,
        ) / _REFS_FILE

    @staticmethod
    def _teams(data: dict) -> list[str]:
        """Return the team list, normalizing legacy ``{"count": n}`` files."""
        teams = data.get("teams")
        if isinstance(teams, list):
            return [str(t) for t in teams if t]
        return []

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError as exc:
            team_logger.warning("refs read %s failed: %s", path, exc)
            return None
        except json.JSONDecodeError:
            team_logger.warning("refs file %s malformed; treating as empty", path)
            return None
        if not isinstance(data, dict):
            return None
        return data

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


__all__ = ["MemberRefStore"]
