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
from typing import Literal

from openjiuwen.agent_teams.paths import team_member_workspace_dir, team_workspace_dir
from openjiuwen.agent_teams.team_workspace.file_content import FileContent, parse_file_content
from openjiuwen.agent_teams.team_workspace.frontmatter import (
    atomic_write,
    body_sha256,
    write_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.layout import WorkspaceLayout
from openjiuwen.agent_teams.tools.database.engine import get_current_time
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
        *,
        now: int | None = None,
    ) -> FileContent | None:
        """Write ``text`` to ``<member_dir>/prompts/identity/member_prompt.md``.

        The file records the body hash as its baseline (``evolved=false`` at
        write time); later edits by the evolution party are detected by hash
        divergence on restart. Returns the file's parsed state (a
        :class:`FileContent` carrying body *and* ``updated_at``) so the caller
        can prime the cache's overlay and mtime channels from one read; the
        value is the evolved state when the file was protected, the newly
        written baseline otherwise, or ``None`` when ``text`` was empty.

        ``now`` is the write timestamp stamped into the frontmatter's
        ``updated_at`` field. When supplied, the caller (assembler) reuses
        the same value to prime the cache's updated_at channel so the file
        and the cache carry one timestamp; when ``None`` it defaults to the
        current time.
        """
        if not text:
            return None
        ts = now if now is not None else get_current_time()
        member_dir = team_member_workspace_dir(team_name, member_name)
        target = WorkspaceLayout.member_prompt_file(member_dir)
        evolved = self._evolved_content(target)
        if evolved is not None:
            team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
            return evolved
        meta = {
            "kind": "prompt",
            "name": "member_prompt",
            "baseline_sha256": body_sha256(text),
            "evolved": False,
            "updated_at": ts,
        }
        self._write(target, meta, text)
        return self._content_from_parts(meta, text, ts)

    def write_card(
        self,
        team_name: str,
        member_name: str,
        desc: str | None,
        *,
        now: int | None = None,
    ) -> FileContent | None:
        """Write the member card to ``prompts/identity/card.md``.

        Body is the plain-text ``desc`` (single field — no JSON wrapper;
        ``display_name`` is not file-evolvable and stays in the DB column).
        Returns the file's parsed state (see :meth:`write_member_prompt`):
        the evolved state when protected, the new baseline otherwise, or
        ``None`` when ``desc`` was empty.

        ``now`` is the write timestamp stamped into ``updated_at`` (see
        :meth:`write_member_prompt`).
        """
        if not desc:
            return None
        ts = now if now is not None else get_current_time()
        member_dir = team_member_workspace_dir(team_name, member_name)
        target = WorkspaceLayout.member_card_file(member_dir)
        evolved = self._evolved_content(target)
        if evolved is not None:
            team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
            return evolved
        meta = {
            "kind": "card",
            "name": "member_card",
            "baseline_sha256": body_sha256(desc),
            "evolved": False,
            "updated_at": ts,
        }
        self._write(target, meta, desc)
        return self._content_from_parts(meta, desc, ts)

    def write_team_prompt(self, team_name: str, text: str | None, *, now: int | None = None) -> FileContent | None:
        """Write ``text`` to ``team-workspace/prompts/identity/team_prompt.md``.

        Single file — no ``.<lang>`` suffix: the team prompt is a single
        user-provided value, not language-mirrored. Returns the file's
        parsed state (see :meth:`write_member_prompt`); the evolved state
        when protected, the new baseline otherwise, or ``None`` when empty.

        ``now`` is the write timestamp stamped into ``updated_at`` (see
        :meth:`write_member_prompt`).
        """
        if not text:
            return None
        ts = now if now is not None else get_current_time()
        target = WorkspaceLayout.team_prompt_file(self.team_workspace_root(team_name))
        evolved = self._evolved_content(target)
        if evolved is not None:
            team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
            return evolved
        meta = {
            "kind": "prompt",
            "name": "team_prompt",
            "baseline_sha256": body_sha256(text),
            "evolved": False,
            "updated_at": ts,
        }
        self._write(target, meta, text)
        return self._content_from_parts(meta, text, ts)

    def write_team_card(
        self,
        team_name: str,
        desc: str | None,
        *,
        now: int | None = None,
    ) -> FileContent | None:
        """Write the team card to ``team-workspace/prompts/identity/team_card.md``.

        Body is the plain-text ``desc`` (``display_name`` stays in the DB).
        Returns the file's parsed state (see :meth:`write_member_prompt`):
        the evolved state when protected, the new baseline otherwise, or
        ``None`` when ``desc`` was empty.

        ``now`` is the write timestamp stamped into ``updated_at`` (see
        :meth:`write_member_prompt`).
        """
        if not desc:
            return None
        ts = now if now is not None else get_current_time()
        target = WorkspaceLayout.team_card_file(self.team_workspace_root(team_name))
        evolved = self._evolved_content(target)
        if evolved is not None:
            team_logger.info("[workspace] %s evolved — write skipped (evolution wins)", target)
            return evolved
        meta = {
            "kind": "team_card",
            "name": "team_card",
            "baseline_sha256": body_sha256(desc),
            "evolved": False,
            "updated_at": ts,
        }
        self._write(target, meta, desc)
        return self._content_from_parts(meta, desc, ts)

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

    # ── updated_at probe (roster mtime overlay) ───────────────────────────
    #
    # The roster mtime probe (``TeamBackend.get_members_max_updated_at``)
    # reads each B-class file's ``updated_at`` frontmatter field and takes
    # ``max(DB updated_at, max(md updated_at))`` so that a member whose md
    # was written (spawn) after its DB row (build_team) still advances the
    # probe and re-delivers the roster with the evolved value. The field is
    # stamped at write time (see ``write_*``); a file missing the field
    # (hand-written or pre-this-change) is backfilled with the current time
    # on first read so the next probe sees a stable value — the backfill
    # touches only the meta, never the body or baseline hash.

    @staticmethod
    def read_member_updated_at(
        team_name: str,
        member_name: str,
        field: Literal["card", "prompt"],
    ) -> int:
        """Return the ``updated_at`` of a member's B-class file (ms), or 0.

        ``field`` selects ``card.md`` (``"card"``) or ``member_prompt.md``
        (``"prompt"``). Missing file → 0 (does not advance the probe). The
        field is backfilled on first read when missing — see
        :func:`file_content.parse_file_content`.
        """
        member_dir = team_member_workspace_dir(team_name, member_name)
        if field == "card":
            path = WorkspaceLayout.member_card_file(member_dir)
        else:
            path = WorkspaceLayout.member_prompt_file(member_dir)
        content = parse_file_content(path)
        return content.updated_at if content is not None else 0

    def read_team_updated_at(
        self,
        team_name: str,
        field: Literal["card", "prompt"],
    ) -> int:
        """Return the ``updated_at`` of a team-level B-class file (ms), or 0."""
        root = self.team_workspace_root(team_name)
        if field == "card":
            path = WorkspaceLayout.team_card_file(root)
        else:
            path = WorkspaceLayout.team_prompt_file(root)
        content = parse_file_content(path)
        return content.updated_at if content is not None else 0

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
    def _evolved_content(path: Path) -> FileContent | None:
        """Write-side evolution protection: return the evolved file's state.

        Returns the file's :class:`FileContent` when it is evolved (body hash
        diverges from its recorded baseline — the evolution party edited it)
        so the caller keeps that value and skips the write. A missing /
        un-evolved / malformed file is writable and returns ``None``
        (missing → baseline write; un-evolved → refresh; malformed →
        rebuild). Reads once via :func:`parse_file_content`, so the
        ``updated_at`` of the protected file is carried back to the caller
        (and into the cache) along with the body.
        """
        try:
            content = parse_file_content(path)
        except ValueError:
            return None  # malformed frontmatter → invalid file, freely rewritable
        if content is None:
            return None  # missing → writable
        return content if content.is_evolved() else None

    @staticmethod
    def _content_from_parts(meta: dict, body: str, updated_at: int) -> FileContent:
        """Build a :class:`FileContent` from the write-side meta + body.

        Used right after a successful write: the parts in hand are the
        ground truth (the file was just written with this meta and body),
        so no re-read is needed to populate the cache.
        """
        return FileContent(
            kind=meta.get("kind", ""),
            name=meta.get("name", ""),
            language=meta.get("language", ""),
            baseline_sha256=meta.get("baseline_sha256"),
            updated_at=updated_at,
            body=body,
            evolved=bool(meta.get("evolved", False)),
        )

    @staticmethod
    def _read_body(path: Path) -> str | None:
        """Read the B-class body iff evolved; info-log the value source.

        ``None`` means "no file value" — the caller falls back to the raw DB
        column. An ``evolved`` line means the workspace value wins over the
        DB column. Reads the file once via :func:`parse_file_content`, which
        yields the body and ``updated_at`` from a single parse (the
        ``updated_at`` backfill lives there too).
        """
        try:
            content = parse_file_content(path)
        except ValueError:
            team_logger.info("[workspace] %s malformed frontmatter — DB value stands", path)
            return None
        if content is None:
            return None
        if content.is_evolved():
            team_logger.info("[workspace] %s evolved — workspace value wins", path)
            return content.body
        team_logger.info("[workspace] %s un-evolved — DB value stands", path)
        return None


__all__ = ["WorkspaceStore"]
