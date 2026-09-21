"""IM learning scheduler coroutine (OJ-02/04/05 integration point).

One asyncio task owns the whole IM learning pipeline for one PersonalContext
home:

1. **fetch stage** — walk the whitelist via ``ImLearningFetchProvider``, one
   lease-managed run per target (``im_stage_runs``, stage='fetch');
2. **index stage** — drain the changelog into the FTS index after every
   successful fetch run, plus a periodic fallback drain, lease-managed with
   the global source key (stage='index').

Synchronous ``sqlite3`` work is wrapped in ``asyncio.to_thread`` (decision
D4).  The scheduler never raises: per-target failures are recorded on the
run rows and in the status surface; stop is a cooperative ``stop_event``.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from openjiuwen.core.common.logging import LogManager

from openjiuwen.harness.personal_context.im.backfill import (
    assess_backfill,
    ensure_backfill_rows,
)
from openjiuwen.harness.personal_context.im.fetch_provider import (
    ImLearningFetchProvider,
    TargetFetchOutcome,
)
from openjiuwen.harness.personal_context.im.fts_consumer import FtsConsumer
from openjiuwen.harness.personal_context.im.models import ImLearningTarget
from openjiuwen.harness.personal_context.im.schema import init_im_schema
from openjiuwen.harness.personal_context.im.source import ImLearningSource
from openjiuwen.harness.personal_context.im.stage_runs import (
    GLOBAL_SOURCE_KEY,
    begin_stage_run,
    finish_stage_run,
    latest_stage_run,
    list_active_runs,
    source_key_for,
)

im_logger = LogManager.get_logger("im_learning")

FETCH_STAGE = "fetch"
INDEX_STAGE = "index"
DEFAULT_FETCH_INTERVAL_SECONDS = 600.0
DEFAULT_INDEX_FALLBACK_SECONDS = 60.0
LEASE_TTL_MS = 600_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def open_im_context_db(home: str | Path) -> sqlite3.Connection:
    """Open (and create) the dedicated ``<home>/im/im_context.db`` connection."""
    db_path = Path(home).expanduser() / "im" / "im_context.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError:
        im_logger.exception("im.scheduler.pragma_failed path=%s", db_path)
    return conn


@dataclass
class ImLearningSchedulerStatus:
    """Bounded snapshot of the IM learning scheduler (status surface)."""

    running: bool = False
    last_cycle_at_ms: Optional[int] = None
    last_cycle_targets: int = 0
    last_cycle_persisted: int = 0
    last_cycle_errors: int = 0
    last_indexed_at_ms: Optional[int] = None
    last_error: Optional[str] = None


class ImLearningScheduler:
    """Own the IM learning fetch+index loop over one ``im_context.db``."""

    def __init__(
        self,
        *,
        source: ImLearningSource,
        home: str | Path,
        targets: tuple[ImLearningTarget, ...],
        since_ms: Optional[int] = None,
        fetch_interval_seconds: float = DEFAULT_FETCH_INTERVAL_SECONDS,
        index_fallback_seconds: float = DEFAULT_INDEX_FALLBACK_SECONDS,
        fetch_top_n: int = 50,
        max_pages: int = 10,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        self._source = source
        self._home = Path(home)
        self._targets = tuple(targets)
        self._since_ms = since_ms
        self._fetch_interval = max(1.0, float(fetch_interval_seconds))
        self._index_fallback = max(1.0, float(index_fallback_seconds))
        self._owns_conn = conn is None
        self._conn = conn if conn is not None else open_im_context_db(self._home)
        self._provider = ImLearningFetchProvider(
            source=source,
            conn=self._conn,
            targets=self._targets,
            since_ms=since_ms,
            fetch_top_n=fetch_top_n,
            max_pages=max_pages,
        )
        self._fts = FtsConsumer(self._conn)
        self._stop_event: Optional[asyncio.Event] = None
        self._wake_event: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._status = ImLearningSchedulerStatus()

    # ------------------------------------------------------------------ state

    @property
    def status(self) -> ImLearningSchedulerStatus:
        return self._status

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def db_path(self) -> Path:
        return self._home / "im" / "im_context.db"

    # -------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Prepare schema/backfill rows and launch the scheduler coroutine."""
        if self.is_running():
            return
        await asyncio.to_thread(init_im_schema, self._conn)
        await asyncio.to_thread(ensure_backfill_rows, self._conn, targets=self._targets)
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._status = ImLearningSchedulerStatus(running=True)
        self._task = asyncio.create_task(self._run(), name="personal-context-im-learning")

    async def stop(self, *, timeout_seconds: float = 10.0) -> None:
        """Cooperatively stop the loop and close the owned connection."""
        task = self._task
        if task is None:
            self._close_conn()
            return
        if self._stop_event is not None:
            self._stop_event.set()
        if self._wake_event is not None:
            self._wake_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        finally:
            self._task = None
            self._stop_event = None
            self._wake_event = None
            self._status.running = False
            self._close_conn()

    def _close_conn(self) -> None:
        if self._owns_conn:
            try:
                self._conn.close()
            except sqlite3.Error:
                im_logger.exception("im.scheduler.conn_close_failed")

    async def trigger_now(self) -> bool:
        """Request one immediate cycle; False when not running."""
        if self._wake_event is None or not self.is_running():
            return False
        self._wake_event.set()
        return True

    # ------------------------------------------------------------------- loop

    async def _run(self) -> None:
        stop_event = self._stop_event
        wake_event = self._wake_event
        assert stop_event is not None and wake_event is not None  # noqa: S101 - loop invariant
        next_fetch_at = 0.0
        next_index_at = 0.0
        loop = asyncio.get_running_loop()
        try:
            while not stop_event.is_set():
                now = loop.time()
                if now >= next_fetch_at:
                    await self._run_cycle()
                    next_fetch_at = loop.time() + self._fetch_interval
                    next_index_at = loop.time() + self._index_fallback
                    continue
                if now >= next_index_at:
                    await self._run_index_stage(now_ms=_now_ms())
                    next_index_at = loop.time() + self._index_fallback
                    continue
                # Sleep until the nearest deadline or an early wake.
                deadline = min(next_fetch_at, next_index_at)
                wait_task = asyncio.ensure_future(asyncio.wait_for(stop_event.wait(), timeout=deadline - now))
                wake_task = asyncio.ensure_future(wake_event.wait())
                try:
                    await asyncio.wait(
                        [wait_task, wake_task],
                        timeout=deadline - loop.time(),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    wait_task.cancel()
                    wake_task.cancel()
                    await asyncio.gather(wait_task, wake_task, return_exceptions=True)
                if wake_event.is_set():
                    wake_event.clear()
                    next_fetch_at = 0.0  # run a cycle immediately
                if stop_event.is_set():
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            im_logger.exception("im.scheduler.loop_failed")
            self._status.last_error = str(exc)
            raise

    async def _run_cycle(self) -> None:
        """One fetch cycle over every target, then one index drain."""
        now_ms = _now_ms()
        self._status.last_cycle_at_ms = now_ms
        outcomes = await self._run_fetch_stage(now_ms=now_ms)
        persisted = sum(outcome.messages_persisted for outcome in outcomes)
        errors = sum(1 for outcome in outcomes if outcome.error)
        self._status.last_cycle_targets = len(outcomes)
        self._status.last_cycle_persisted = persisted
        self._status.last_cycle_errors = errors
        if persisted > 0 or errors < len(outcomes):
            # Drain the outbox right after fetch (fast path); the periodic
            # fallback drain covers messages persisted by other processes.
            await self._run_index_stage(now_ms=now_ms)

    async def _run_fetch_stage(self, *, now_ms: int) -> tuple[TargetFetchOutcome, ...]:
        outcomes: list[TargetFetchOutcome] = []
        for target in self._targets:
            source_key = source_key_for(target.channel_id, target.kind, target.external_id)
            run_id = await asyncio.to_thread(
                begin_stage_run,
                self._conn,
                stage=FETCH_STAGE,
                source_key=source_key,
                lease_ttl_ms=LEASE_TTL_MS,
                now_ms=now_ms,
            )
            if run_id is None:
                # An active run holds the lease (another process / a stuck
                # cycle): skip this target, next cycle retries.
                continue
            try:
                outcome = await self._provider.fetch_target(target, now_ms=now_ms)
            except Exception as exc:  # noqa: BLE001
                im_logger.exception(
                    "im.scheduler.fetch_failed channel=%s kind=%s external_id=%s",
                    target.channel_id,
                    target.kind,
                    target.external_id,
                )
                await asyncio.to_thread(
                    finish_stage_run,
                    self._conn,
                    run_id=run_id,
                    succeeded=False,
                    error=str(exc),
                    now_ms=now_ms,
                )
                outcomes.append(TargetFetchOutcome(target=target, mode="unknown", error=str(exc)))
                continue
            error = outcome.error
            await asyncio.to_thread(
                finish_stage_run,
                self._conn,
                run_id=run_id,
                succeeded=error is None,
                error=error,
                now_ms=now_ms,
            )
            outcomes.append(outcome)
        return tuple(outcomes)

    async def _run_index_stage(self, *, now_ms: int) -> int:
        run_id = await asyncio.to_thread(
            begin_stage_run,
            self._conn,
            stage=INDEX_STAGE,
            source_key=GLOBAL_SOURCE_KEY,
            lease_ttl_ms=LEASE_TTL_MS,
            now_ms=now_ms,
        )
        if run_id is None:
            return 0
        try:
            processed = await asyncio.to_thread(self._fts.drain_once, now_ms=now_ms)
        except Exception as exc:  # noqa: BLE001
            im_logger.exception("im.scheduler.index_failed")
            self._status.last_error = str(exc)
            await asyncio.to_thread(
                finish_stage_run,
                self._conn,
                run_id=run_id,
                succeeded=False,
                error=str(exc),
                now_ms=now_ms,
            )
            return 0
        await asyncio.to_thread(
            finish_stage_run,
            self._conn,
            run_id=run_id,
            succeeded=True,
            now_ms=now_ms,
        )
        if processed > 0:
            self._status.last_indexed_at_ms = now_ms
        return processed

    # ----------------------------------------------------------------- status

    async def read_status(self) -> dict[str, object]:
        """Compose the persisted-state status surface (backfill readiness)."""
        backfill, active = await asyncio.to_thread(self._read_persisted_status)
        return {
            "running": self.is_running(),
            "db_path": str(self.db_path()),
            "targets": len(self._targets),
            "backfill": backfill,
            "active_runs": [
                {
                    "id": run.id,
                    "stage": run.stage,
                    "source_key": run.source_key,
                    "status": run.status,
                    "lease_expires_at_ms": run.lease_expires_at_ms,
                }
                for run in active
            ],
            "last_cycle_at_ms": self._status.last_cycle_at_ms,
            "last_cycle_targets": self._status.last_cycle_targets,
            "last_cycle_persisted": self._status.last_cycle_persisted,
            "last_cycle_errors": self._status.last_cycle_errors,
            "last_indexed_at_ms": self._status.last_indexed_at_ms,
            "last_error": self._status.last_error,
        }

    def _read_persisted_status(self) -> tuple[dict[str, object], list[Any]]:
        """Read persisted state, reopening the DB when the loop is stopped."""
        conn = self._conn
        if not self._owns_conn:
            backfill = assess_backfill(conn, targets=self._targets)
            active = list_active_runs(conn)
            return backfill, active
        if self.is_running():
            backfill = assess_backfill(conn, targets=self._targets)
            active = list_active_runs(conn)
            return backfill, active
        # Stopped and we own (and closed) the connection: reopen read-only.
        reopened = open_im_context_db(self._home)
        try:
            backfill = assess_backfill(reopened, targets=self._targets)
            active = list_active_runs(reopened)
            return backfill, active
        finally:
            try:
                reopened.close()
            except sqlite3.Error:
                im_logger.exception("im.scheduler.status_close_failed")

    async def latest_fetch_run(self, target: ImLearningTarget) -> Optional[Any]:
        """Newest stage run snapshot for one target (status surface helper)."""
        source_key = source_key_for(target.channel_id, target.kind, target.external_id)
        conn = self._conn if (not self._owns_conn or self.is_running()) else open_im_context_db(self._home)
        try:
            return await asyncio.to_thread(
                latest_stage_run,
                conn,
                stage=FETCH_STAGE,
                source_key=source_key,
            )
        finally:
            if conn is not self._conn:
                try:
                    conn.close()
                except sqlite3.Error:
                    im_logger.exception("im.scheduler.status_close_failed")


__all__ = [
    "FETCH_STAGE",
    "INDEX_STAGE",
    "ImLearningScheduler",
    "ImLearningSchedulerStatus",
    "open_im_context_db",
]
