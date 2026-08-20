# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""B-class value files: member/team static values on disk.

The static long-text fields — ``team_member.desc`` / ``prompt`` and
``team.desc`` — are dual-written: the DB columns keep their raw values
(fallback) and the files hold the evolvable value. Reads prefer the file
value when the file exists and its body hash diverges from the recorded
baseline; otherwise the raw DB value stands.

``display_name`` is **not** file-evolvable — the member name is assembled at
spawn time (``<team>_<member>`` or the caller's raw value) and always falls
back to the DB column.

Layout:

    <member_dir>/prompts/identity/card.md          # body = desc only (plain text)
    <member_dir>/prompts/identity/member_prompt.md # prompt text
    <team>/team-workspace/prompts/identity/team_card.md   # body = desc only
    <team>/team-workspace/prompts/identity/team_prompt.md # team prompt (single file)
"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.agent_teams.paths import team_member_workspace_dir, team_workspace_dir
from openjiuwen.agent_teams.team_workspace.frontmatter import (
    atomic_write,
    body_sha256,
    is_evolved,
    read_frontmatter,
    write_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.layout import WorkspaceLayout
from openjiuwen.core.common.logging import team_logger


class WorkspaceStore:
    """Write / read the B-class value files for member/team static fields.

    Writes happen once at spawn (the only writer). A failed write degrades to
    "DB only, no file" — spawn never fails over persistence.

    Write-side evolution protection: an already-evolved file (body hash != its
    recorded baseline) is never overwritten — the evolution party's edit wins
    over a newer user input. A file whose body hash still equals its baseline
    (un-evolved) is freely re-written with the new value.
    """

    # Paths come straight from ``agent_teams.paths``: member dirs via
    # ``team_member_workspace_dir``, team root via ``team_workspace_dir``.
    # No per-store path state.

    # ── write side ─────────────────────────────────────────────────────────

    def write_member_prompt(
        self,
        team_name: str,
        member_name: str,
        text: str | None,
    ) -> None:
        """Write ``text`` to ``<member_dir>/prompts/identity/member_prompt.md``.

        The file records the body hash as its baseline (``evolved=false`` at
        write time); later edits by the evolution party are detected by hash
        divergence on restart.
        """
        if not text:
            return
        member_dir = team_member_workspace_dir(team_name, member_name)
        target = WorkspaceLayout.member_prompt_file(member_dir)
        if not self._may_write(target):
            return
        meta = {
            "kind": "prompt",
            "name": "member_prompt",
            "baseline_sha256": body_sha256(text),
            "evolved": False,
        }
        self._write(target, meta, text)

    def write_card(
        self,
        team_name: str,
        member_name: str,
        desc: str | None,
    ) -> None:
        """Write the member card to ``prompts/identity/card.md``.

        Body is the plain-text ``desc`` (single field — no JSON wrapper;
        ``display_name`` is not file-evolvable and stays in the DB column).
        """
        if not desc:
            return
        member_dir = team_member_workspace_dir(team_name, member_name)
        target = WorkspaceLayout.member_card_file(member_dir)
        if not self._may_write(target):
            return
        meta = {
            "kind": "card",
            "name": "member_card",
            "baseline_sha256": body_sha256(desc),
            "evolved": False,
        }
        self._write(target, meta, desc)

    def write_team_prompt(self, team_name: str, text: str | None) -> None:
        """Write ``text`` to ``team-workspace/prompts/identity/team_prompt.md``.

        Single file — no ``.<lang>`` suffix: the team prompt is a single
        user-provided value, not language-mirrored.
        """
        if not text:
            return
        target = WorkspaceLayout.team_prompt_file(self.team_workspace_root(team_name))
        if not self._may_write(target):
            return
        meta = {
            "kind": "prompt",
            "name": "team_prompt",
            "baseline_sha256": body_sha256(text),
            "evolved": False,
        }
        self._write(target, meta, text)

    def write_team_card(
        self,
        team_name: str,
        desc: str | None,
    ) -> None:
        """Write the team card to ``team-workspace/prompts/identity/team_card.md``.

        Body is the plain-text ``desc`` (``display_name`` stays in the DB).
        """
        if not desc:
            return
        target = WorkspaceLayout.team_card_file(self.team_workspace_root(team_name))
        if not self._may_write(target):
            return
        meta = {
            "kind": "team_card",
            "name": "team_card",
            "baseline_sha256": body_sha256(desc),
            "evolved": False,
        }
        self._write(target, meta, desc)

    # ── read side ──────────────────────────────────────────────────────────

    def read_card(
        self,
        team_name: str,
        member_name: str,
    ) -> str | None:
        """Read the member card's ``desc`` body.

        Returns ``None`` when the file is missing or un-evolved (the caller
        falls back to the raw DB column).
        """
        member_dir = team_member_workspace_dir(team_name, member_name)
        return self._read_body(WorkspaceLayout.member_card_file(member_dir))

    def read_team_card(self, team_name: str) -> str | None:
        """Read the team card's ``desc`` body."""
        return self._read_body(WorkspaceLayout.team_card_file(self.team_workspace_root(team_name)))

    def read_member_prompt(
        self,
        team_name: str,
        member_name: str,
    ) -> str | None:
        """Read the member prompt body (``member_prompt.md``)."""
        member_dir = team_member_workspace_dir(team_name, member_name)
        return self._read_body(WorkspaceLayout.member_prompt_file(member_dir))

    def read_team_prompt(self, team_name: str) -> str | None:
        """Read the team prompt body (``team_prompt.md``)."""
        return self._read_body(WorkspaceLayout.team_prompt_file(self.team_workspace_root(team_name)))

    @staticmethod
    def team_workspace_root(team_name: str) -> Path:
        """Return the team's evolvable workspace root (``team-workspace/``).

        A/C-class files live under ``prompts/system/`` and ``prompts/tool/``;
        B-class team files under ``prompts/identity/``. The
        ``WorkspaceCache`` scans these directories at build time.
        """
        return team_workspace_dir(team_name)

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _write(target: Path, meta: dict, body: str) -> None:
        """Write one file, degrading on failure instead of raising.

        Class contract (see module docstring): a failed write degrades to
        "DB only, no file" — spawn never fails over persistence. Mirrors the
        read side's warning + fallback style.
        """
        try:
            atomic_write(target, write_frontmatter(meta, body))
        except OSError as exc:
            team_logger.warning("workspace write %s failed (DB value stands): %s", target, exc)

    @staticmethod
    def _may_write(path: Path) -> bool:
        """Write-side evolution protection: never clobber an evolved file.

        Returns False when the target exists and its body hash diverges from
        its recorded baseline (evolution party edited it) — the new user input
        does not overwrite the evolved value. Un-evolved or missing files are
        writable (missing → baseline write; un-evolved → refresh).
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return True
        try:
            meta, body = read_frontmatter(text)
        except ValueError:
            return True  # malformed frontmatter → invalid file, freely rewritable
        return not is_evolved(meta, body)

    @staticmethod
    def _read_body(path: Path) -> str | None:
        """Read the B-class body iff evolved; info-log the value source.

        ``None`` means "no file value" — the caller falls back to the raw DB
        column. An ``evolved`` line means the workspace value wins over the
        DB column.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            team_logger.info("[workspace] %s missing — DB value stands", path)
            return None
        except OSError as exc:
            team_logger.warning("prompt read %s failed: %s", path, exc)
            return None
        try:
            meta, body = read_frontmatter(text)
        except ValueError:
            team_logger.info("[workspace] %s malformed frontmatter — DB value stands", path)
            return None
        if is_evolved(meta, body):
            team_logger.info("[workspace] %s evolved — workspace value wins", path)
            return body
        team_logger.info("[workspace] %s un-evolved — DB value stands", path)
        return None


__all__ = ["WorkspaceStore"]
