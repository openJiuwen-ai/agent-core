# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session-scoped content file store.

message/task ``content`` moves out of SQLite into session files; the DB
``content`` column stores only the placeholder ``#file#`` and DAOs
transparently dereference it — the file path is derived from the row's own
fields (kind + object id + to-member), never stored. Callers only ever see
the body text.

The store is strictly session-scoped: every file lives under
``paths.team_session_dir(team_name, session_id)`` and is reclaimed with the
session. Static prompt/tool/card fields live under member/team roots
(handled by the workspace cache, not here). The store resolves paths through
the shared ``agent_teams.paths`` helpers; no extra root abstraction — there
is exactly one root kind (session), so no provider interface exists.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from openjiuwen.agent_teams.paths import team_session_dir

# DB ``content`` column value marking "body lives in the session file".
# No path is stored — ``SessionFileStore.get`` derives it from the row fields.
CONTENT_IN_FILE = "#file#"

# FileAddress.kind values — the path-derivation branch key.
KIND_DIRECT = "direct"
KIND_BROADCAST = "broadcast"
KIND_TASK = "task"


@dataclass(frozen=True)
class FileAddress:
    """Structured identity of a write target; callers never hand-build paths."""

    team_name: str
    session_id: str
    kind: str  # KIND_DIRECT | KIND_BROADCAST | KIND_TASK
    object_id: str
    to_member: str | None = None


class SessionFileStore:
    """Write message/task bodies to session files; DB keeps ``#file#`` only.

    ``put`` atomically writes and returns the placeholder; ``get`` derives
    the logical path from ``FileAddress`` (kind + object id + to-member) and
    reads the file back. The DAO only calls ``get`` for rows whose content is
    the placeholder — historical inline rows stay inline. All paths resolve
    under the session root, so the store is bound to the session lifecycle.
    """

    # ── write ──────────────────────────────────────────────────────────────

    def put(self, text: str, address: FileAddress) -> str:
        """Write ``text`` and return the ``#file#`` placeholder for the DB.

        Raises:
            OSError: on IO failure — the DAO layer decides the degradation
                (falls back to storing the raw text inline).
        """
        target = self._resolve(address)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
        return CONTENT_IN_FILE

    # ── read ───────────────────────────────────────────────────────────────

    def get(self, address: FileAddress) -> str:
        """Derive the file path from ``address`` and return its body text.

        The logical path is fully determined by the address fields (see
        :meth:`_logical_path`), so the DB never carries a pointer. A path
        escaping the team session root raises ``ValueError``; a missing file
        raises ``FileNotFoundError`` (with team/session/address diagnostics)
        so a dangling placeholder never surfaces as a silent ``#file#``.
        """
        target = self._resolve(address)
        try:
            return target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"content file missing (team={address.team_name}, session={address.session_id}, "
                f"kind={address.kind}, object_id={address.object_id})"
            ) from exc

    # ── cleanup ────────────────────────────────────────────────────────────

    def remove_session(self, *, team_name: str, session_id: str) -> None:
        """Delete the session's ``messages/`` and ``tasks/`` directories."""
        root = self._session_root(team_name, session_id)
        for sub in ("messages", "tasks"):
            target = root / sub
            if target.is_dir():
                _rmtree(target)

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _session_root(team_name: str, session_id: str) -> Path:
        return team_session_dir(team_name, session_id)

    def _resolve(self, address: FileAddress) -> Path:
        """Resolve and sanity-check the absolute file path for ``address``."""
        logical = self._logical_path(address)
        root = self._session_root(address.team_name, address.session_id)
        target = (root / logical).resolve()
        root_resolved = root.resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(
                f"file path escapes session root (team={address.team_name}, kind={address.kind}, "
                f"object_id={address.object_id})"
            ) from exc
        return target

    @staticmethod
    def _logical_path(address: FileAddress) -> str:
        if address.kind == KIND_DIRECT:
            if not address.to_member:
                raise ValueError("direct message requires to_member")
            return f"messages/to_{address.to_member}/{address.object_id}.md"
        if address.kind == KIND_BROADCAST:
            return f"messages/broadcast/{address.object_id}.md"
        if address.kind == KIND_TASK:
            return f"tasks/{address.object_id}.md"
        raise ValueError(f"unknown kind: {address.kind}")


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


__all__ = ["CONTENT_IN_FILE", "KIND_BROADCAST", "KIND_DIRECT", "KIND_TASK", "FileAddress", "SessionFileStore"]
