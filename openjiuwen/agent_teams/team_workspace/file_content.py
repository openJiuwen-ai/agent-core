# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parsed state of one evolvable workspace file.

A single file read produces a :class:`FileContent` carrying the body, the
recorded ``baseline_sha256``, the frontmatter ``updated_at`` (the roster
mtime probe — a member whose md was written after its DB row advances the
probe and re-delivers the roster with the evolved value), and the evolved
flag. Three orthogonal concerns route through one read:

- *Read side — evolved overlay*: :meth:`FileContent.is_evolved` judges body
  hash divergence from the baseline (a file without a baseline — hand-written
  — is always evolved). The cache / store serve the file body only when it
  is evolved, else fall back to the code default / raw DB column.
- *Read side — roster mtime probe*: ``updated_at`` is the frontmatter field
  the probe reads (not the filesystem mtime). A file missing the field
  (hand-written or pre-this-change) is backfilled with the current time on
  first read so the next probe sees a stable value — the backfill touches
  only the meta, never the body or baseline hash.
- *Write side — evolution protection*: the write path reads the existing
  file's evolved state so an already-evolved file (the evolution party's
  edit) is never overwritten by a newer user input.

Malformed frontmatter (YAML parse failure or a non-mapping root) raises
``ValueError`` — callers treat such files as *invalid*: the read side falls
back to the framework default / DB value, the write side rebuilds the
baseline. A missing file returns ``None``.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from openjiuwen.agent_teams.team_workspace.frontmatter import (
    body_sha256,
    read_frontmatter,
)
from openjiuwen.core.common.logging import team_logger


class FileContent(NamedTuple):
    """Parsed state of one evolvable workspace file.

    Fields mirror the frontmatter meta (``kind`` / ``name`` / ``language`` /
    ``baseline_sha256`` / ``evolved``) plus the body and the frontmatter
    ``updated_at`` (ms) the roster mtime probe reads. ``language`` is empty
    for B-class files (they carry no language suffix); A/C files carry it.
    """

    kind: str
    name: str
    language: str
    baseline_sha256: str | None
    updated_at: int
    body: str
    evolved: bool
    # Whether the frontmatter carried an explicit, parseable ``updated_at``
    # integer. ``False`` means the field was absent / blank / non-int (a
    # hand-evolved file that edited the body without stamping the field).
    # The member-prompt re-announce probe treats ``updated_at == 0`` with
    # ``updated_at_present=False`` as an explicit "must update" signal —
    # distinct from ``updated_at == 0`` of a *missing* file (``present=True``
    # via the caller's ``None`` → 0 fallback), so a blank field forces a
    # one-shot re-delivery instead of relying on a backfilled wall-clock
    # value happening to advance past the baseline.
    updated_at_present: bool = True

    def is_evolved(self) -> bool:
        """Return whether the body has diverged from its recorded baseline.

        A file without a baseline (hand-written, no frontmatter) is always
        evolved — its body always wins, backward-compatible with hand-edited
        md. The ``evolved`` field is the write-time initial state only (always
        ``False``); the read side never trusts it.
        """
        if self.baseline_sha256 is None:
            return True
        return body_sha256(self.body) != self.baseline_sha256


def parse_file_content(path: Path) -> FileContent | None:
    """Read and parse one evolvable workspace file.

    Returns ``None`` when the file is missing. Raises ``ValueError`` when the
    frontmatter is malformed (YAML parse failure or a non-mapping root) so
    callers treat the file as invalid. Backfills a missing ``updated_at``
    field with the current time (persisted, meta only) so the next roster
    mtime probe sees a stable value.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        team_logger.warning("[workspace] %s unreadable: %s", path, exc)
        return None
    # A file without a ``---`` block is hand-written — its body always wins
    # but the backfill must never touch it (S_24 invariant 2: hand-written
    # files are not stamped, updated_at stays 0). read_frontmatter collapses
    # "no block" and "empty block" to the same empty dict, so block presence
    # is checked on the raw text before parsing.
    has_frontmatter_block = text.startswith("---")
    meta, body = read_frontmatter(text)
    updated_at_present = False
    if has_frontmatter_block:
        updated_at = meta.get("updated_at")
        if isinstance(updated_at, int) and updated_at >= 0:
            updated_at_present = True
        else:
            # Blank / absent / non-int ``updated_at``: return 0 faithfully
            # rather than backfilling a wall-clock value. The member-prompt and
            # team-card re-announce probes treat ``(0, present=False)`` as an
            # explicit "must update" signal and stamp a single timestamp back
            # into the file + baseline in one move (so the next read sees a
            # stable non-zero value and does not re-fire). The aggregated
            # roster probe floors on the DB ``updated_at`` (``max(db, 0)`` →
            # ``db``) — it has no per-file stamp path, so a blank field does
            # not advance it directly; it moves only when a member_prompt /
            # team_card stamp (or a member DB write) pushes the aggregate.
            updated_at = 0
    else:
        updated_at = 0
    return FileContent(
        kind=meta.get("kind", ""),
        name=meta.get("name", ""),
        language=meta.get("language", ""),
        baseline_sha256=meta.get("baseline_sha256"),
        updated_at=updated_at,
        body=body,
        evolved=bool(meta.get("evolved", False)),
        updated_at_present=updated_at_present,
    )


__all__ = ["FileContent", "parse_file_content"]
