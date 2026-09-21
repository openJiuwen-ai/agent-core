"""Generic staged run/lease state machine over ``im_stage_runs``.

One row per (stage, source_key) run.  Stages: ``fetch`` / ``index`` /
``distill`` (the distill stage arrives with OJ-08 and reuses this module).

Guarantees (migration plan §5.3):

- single active run per (stage, source_key): a partial unique index rejects
  a second ``running`` row;
- lease takeover: an expired lease may be claimed by a new run; duplicate
  execution is de-duplicated downstream by the message unique key
  (channel_id, msg_id);
- statuses: pending -> running -> succeeded / failed.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from openjiuwen.harness.personal_context.im.schema import init_im_schema

STAGES = ("fetch", "index", "distill")
GLOBAL_SOURCE_KEY = "-"


@dataclass(frozen=True)
class StageRunSnapshot:
    id: str
    stage: str
    source_key: str
    status: str
    attempt: int
    lease_owner: Optional[str]
    lease_expires_at_ms: Optional[int]
    started_at_ms: Optional[int]
    finished_at_ms: Optional[int]
    last_error: Optional[str]


def source_key_for(channel_id: str, target_kind: str, external_id: str) -> str:
    """Pack a per-target source key; use ``GLOBAL_SOURCE_KEY`` for global stages."""
    return f"{channel_id}:{target_kind}:{external_id}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row_to_snapshot(row: sqlite3.Row | tuple) -> StageRunSnapshot:
    return StageRunSnapshot(
        id=str(row[0]),
        stage=str(row[1]),
        source_key=str(row[2]),
        status=str(row[3]),
        attempt=int(row[4]),
        lease_owner=str(row[5]) if row[5] else None,
        lease_expires_at_ms=int(row[6]) if row[6] is not None else None,
        started_at_ms=int(row[7]) if row[7] is not None else None,
        finished_at_ms=int(row[8]) if row[8] is not None else None,
        last_error=str(row[9]) if row[9] else None,
    )


_SELECT_COLUMNS = (
    "id, stage, source_key, status, attempt, lease_owner, lease_expires_at_ms, "
    "started_at_ms, finished_at_ms, last_error"
)


def _active_run(conn: sqlite3.Connection, *, stage: str, source_key: str, now_ms: int) -> Optional[StageRunSnapshot]:
    row = conn.execute(
        f"""
        SELECT {_SELECT_COLUMNS} FROM im_stage_runs
        WHERE stage = ? AND source_key = ? AND status IN ('pending', 'running')
        ORDER BY created_at_ms DESC LIMIT 1
        """,
        (stage, source_key),
    ).fetchone()
    if row is None:
        return None
    snapshot = _row_to_snapshot(row)
    # Lease takeover: a running run whose lease expired may be claimed later
    # by a fresh begin_stage_run (single active constraint enforced below).
    if snapshot.status == "running" and snapshot.lease_expires_at_ms is not None:
        if int(snapshot.lease_expires_at_ms) < now_ms:
            return None
    if snapshot.status == "pending":
        return None
    return snapshot


def begin_stage_run(
    conn: sqlite3.Connection,
    *,
    stage: str,
    source_key: str,
    lease_ttl_ms: int = 600_000,
    window_key: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> Optional[str]:
    """Try to begin a run.  Returns the run id, or None when one is active.

    The single-active constraint is enforced inside one transaction: the
    SELECT of an active run and the INSERT of the new row cannot interleave
    in BEGIN IMMEDIATE.
    """
    if stage not in STAGES:
        raise ValueError(f"unsupported stage: {stage}")
    init_im_schema(conn)
    ts = int(now_ms if now_ms is not None else _now_ms())
    run_id = f"{ts:x}-{uuid4().hex[:12]}"
    lease_owner = uuid4().hex
    conn.execute("BEGIN IMMEDIATE")
    try:
        active = conn.execute(
            """
            SELECT id, status, lease_expires_at_ms FROM im_stage_runs
            WHERE stage = ? AND source_key = ? AND status IN ('pending', 'running')
            ORDER BY created_at_ms DESC LIMIT 1
            """,
            (stage, source_key),
        ).fetchone()
        if active is not None:
            status = str(active[1])
            expires = active[2]
            lease_valid = status == "running" and expires is not None and int(expires) >= ts
            if status == "pending" or lease_valid:
                conn.rollback()
                return None
            # Expired lease: mark the stale run failed, then claim.
            conn.execute(
                "UPDATE im_stage_runs SET status = 'failed', last_error = 'lease expired', "
                "finished_at_ms = ?, updated_at_ms = ? WHERE id = ?",
                (ts, ts, str(active[0])),
            )
        conn.execute(
            """
            INSERT INTO im_stage_runs (
                id, stage, source_key, status, run_token, lease_owner,
                lease_expires_at_ms, attempt, window_key, started_at_ms,
                created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, 'running', ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                run_id,
                stage,
                source_key,
                uuid4().hex,
                lease_owner,
                ts + int(lease_ttl_ms),
                window_key,
                ts,
                ts,
                ts,
            ),
        )
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise


def finish_stage_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    succeeded: bool,
    error: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> None:
    """Mark a run succeeded/failed.  Unknown ids are a no-op (idempotent)."""
    ts = int(now_ms if now_ms is not None else _now_ms())
    status = "succeeded" if succeeded else "failed"
    conn.execute(
        """
        UPDATE im_stage_runs
        SET status = ?, finished_at_ms = ?, last_error = ?, updated_at_ms = ?,
            lease_owner = NULL, lease_expires_at_ms = NULL
        WHERE id = ? AND status = 'running'
        """,
        (status, ts, error, ts, run_id),
    )
    conn.commit()


def renew_lease(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    lease_ttl_ms: int = 600_000,
    now_ms: Optional[int] = None,
) -> bool:
    """Extend the run's lease (heartbeat).  Returns False when not running."""
    ts = int(now_ms if now_ms is not None else _now_ms())
    cursor = conn.execute(
        """
        UPDATE im_stage_runs
        SET lease_expires_at_ms = ?, updated_at_ms = ?
        WHERE id = ? AND status = 'running'
        """,
        (ts + int(lease_ttl_ms), ts, run_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def latest_stage_run(
    conn: sqlite3.Connection,
    *,
    stage: str,
    source_key: str,
) -> Optional[StageRunSnapshot]:
    """Return the newest run snapshot for a (stage, source_key), any status."""
    row = conn.execute(
        f"""
        SELECT {_SELECT_COLUMNS} FROM im_stage_runs
        WHERE stage = ? AND source_key = ?
        ORDER BY created_at_ms DESC LIMIT 1
        """,
        (stage, source_key),
    ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def list_active_runs(conn: sqlite3.Connection) -> list[StageRunSnapshot]:
    rows = conn.execute(
        f"""
        SELECT {_SELECT_COLUMNS} FROM im_stage_runs
        WHERE status IN ('pending', 'running')
        ORDER BY created_at_ms ASC
        """
    ).fetchall()
    return [_row_to_snapshot(row) for row in rows]


__all__ = [
    "GLOBAL_SOURCE_KEY",
    "STAGES",
    "StageRunSnapshot",
    "begin_stage_run",
    "finish_stage_run",
    "latest_stage_run",
    "list_active_runs",
    "renew_lease",
    "source_key_for",
]
