"""Backfill state + newest_seen watermark over ``im_learning_backfill``.

One row per (channel_id, target_kind, external_id).  States:

- ``pending``   initial; the historical window has not been covered yet;
- ``truncated`` page limit hit before the floor was reached; the next cycle
  resumes from ``oldest_msg_id``;
- ``complete``  the backfill window is covered; steady-state incremental
  fetch takes over, driven by ``newest_seen_msg_id`` / ``newest_seen_sent_at``
  (decision D3).

The ``record_backfill_result`` write happens in the same transaction scope
as the caller's persist commit path (the provider commits the connection
right after), so the watermark never advances past uncommitted messages.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from openjiuwen.harness.personal_context.im.models import ImLearningTarget
from openjiuwen.harness.personal_context.im.schema import init_im_schema


@dataclass(frozen=True)
class BackfillState:
    """Immutable snapshot of one target's backfill/watermark state."""

    channel_id: str
    target_kind: str
    external_id: str
    status: str  # 'pending' | 'complete' | 'truncated'
    oldest_sent_at_ms: Optional[int] = None
    oldest_msg_id: Optional[str] = None
    newest_seen_msg_id: Optional[str] = None
    newest_seen_sent_at: Optional[int] = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def read_backfill_state(conn: sqlite3.Connection, *, target: ImLearningTarget) -> Optional[BackfillState]:
    """Return the persisted state of the target, or None when never fetched."""
    row = conn.execute(
        """
        SELECT status, oldest_sent_at_ms, oldest_msg_id,
               newest_seen_msg_id, newest_seen_sent_at
        FROM im_learning_backfill
        WHERE channel_id = ? AND target_kind = ? AND external_id = ?
        """,
        (target.channel_id, target.kind, target.external_id),
    ).fetchone()
    if row is None:
        return None
    return BackfillState(
        channel_id=target.channel_id,
        target_kind=target.kind,
        external_id=target.external_id,
        status=str(row[0]),
        oldest_sent_at_ms=int(row[1]) if row[1] is not None else None,
        oldest_msg_id=str(row[2]) if row[2] else None,
        newest_seen_msg_id=str(row[3]) if row[3] else None,
        newest_seen_sent_at=int(row[4]) if row[4] is not None else None,
    )


def record_backfill_result(
    conn: sqlite3.Connection,
    *,
    target: ImLearningTarget,
    status: str,
    truncated: bool,
    oldest_sent_at_ms: Optional[int] = None,
    oldest_msg_id: Optional[str] = None,
    newest_seen_msg_id: Optional[str] = None,
    newest_seen_sent_at: Optional[int] = None,
    now_ms: Optional[int] = None,
) -> str:
    """Upsert one target's backfill/watermark state; never downgrades ``complete``.

    ``newest_seen_*`` columns only advance (max), so an older snapshot can
    never rewind the watermark (decision D3).
    """
    if status not in ("pending", "complete", "truncated"):
        raise ValueError(f"unsupported backfill status: {status}")
    init_im_schema(conn)
    ts = int(now_ms if now_ms is not None else _now_ms())
    current = read_backfill_state(conn, target=target)
    if current is not None and current.status == "complete" and status != "complete":
        return "complete"
    # Watermark monotonicity: keep the max of current and incoming.
    if current is not None and current.newest_seen_sent_at is not None:
        if newest_seen_sent_at is None or int(current.newest_seen_sent_at) > int(newest_seen_sent_at):
            newest_seen_sent_at = int(current.newest_seen_sent_at)
            newest_seen_msg_id = current.newest_seen_msg_id
    conn.execute(
        """
        INSERT INTO im_learning_backfill (
            channel_id, target_kind, external_id, status, truncated,
            oldest_sent_at_ms, oldest_msg_id, newest_seen_msg_id,
            newest_seen_sent_at, query_count, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        ON CONFLICT(channel_id, target_kind, external_id) DO UPDATE SET
            status = excluded.status,
            truncated = excluded.truncated,
            oldest_sent_at_ms = excluded.oldest_sent_at_ms,
            oldest_msg_id = excluded.oldest_msg_id,
            newest_seen_msg_id = excluded.newest_seen_msg_id,
            newest_seen_sent_at = excluded.newest_seen_sent_at,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            target.channel_id,
            target.kind,
            target.external_id,
            status,
            1 if truncated else 0,
            oldest_sent_at_ms,
            oldest_msg_id,
            newest_seen_msg_id,
            newest_seen_sent_at,
            ts,
        ),
    )
    conn.commit()
    return status


def ensure_backfill_rows(
    conn: sqlite3.Connection,
    *,
    targets: tuple[ImLearningTarget, ...] | list[ImLearningTarget],
) -> None:
    """Create ``pending`` rows for whitelist targets that have none yet.

    Called by the scheduler at startup so the UI/status surface can show
    every learning target before its first fetch.
    """
    init_im_schema(conn)
    for target in targets:
        row = conn.execute(
            """
            SELECT 1 FROM im_learning_backfill
            WHERE channel_id = ? AND target_kind = ? AND external_id = ?
            """,
            (target.channel_id, target.kind, target.external_id),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO im_learning_backfill (
                    channel_id, target_kind, external_id, status, truncated, updated_at_ms
                ) VALUES (?, ?, ?, 'pending', 0, ?)
                """,
                (target.channel_id, target.kind, target.external_id, _now_ms()),
            )
    conn.commit()


def assess_backfill(
    conn: sqlite3.Connection,
    *,
    targets: tuple[ImLearningTarget, ...] | list[ImLearningTarget],
) -> dict[str, object]:
    """Summarize backfill readiness across the whitelist (status surface)."""
    pending: list[dict[str, str]] = []
    truncated: list[dict[str, str]] = []
    for target in targets:
        state = read_backfill_state(conn, target=target)
        if state is None or state.status == "pending":
            pending.append({"channel_id": target.channel_id, "kind": target.kind, "external_id": target.external_id})
        elif state.status == "truncated":
            truncated.append({"channel_id": target.channel_id, "kind": target.kind, "external_id": target.external_id})
    return {
        "ready": not pending and not truncated,
        "pending": pending,
        "truncated": truncated,
    }


__all__ = [
    "BackfillState",
    "assess_backfill",
    "ensure_backfill_rows",
    "read_backfill_state",
    "record_backfill_result",
]
