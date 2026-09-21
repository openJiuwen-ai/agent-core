"""IM learning fetch provider: whitelist walk + persist + watermark advance.

Per-target flow (migration plan §5.2):

1. read the backfill state of the target (OJ-04 ``backfill.py``);
2. pick the fetch mode:
   - ``pending`` / ``truncated`` (backfill window not yet covered): page
     down from newest until the ``since_ms`` floor, resuming from the
     ``oldest_msg_id`` cursor when truncated;
   - ``complete`` (steady state): page down from newest until the
     newest_seen watermark (decision D3);
3. tag ``learning_eligible`` (``learning_scope``, decision D6);
4. persist through ``ImCorpusSink`` (single transaction, OJ-03);
5. only after a successful commit, advance the backfill / newest-seen
   state ("each stage advances its own cursor only after commit").
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from openjiuwen.core.common.logging import LogManager

from openjiuwen.harness.personal_context.im.backfill import (
    BackfillState,
    read_backfill_state,
    record_backfill_result,
)
from openjiuwen.harness.personal_context.im.fetch_depth import (
    FetchPageRangeResult,
    backfill_stop_predicate,
    fetch_page_range,
    newest_seen_stop_predicate,
)
from openjiuwen.harness.personal_context.im.learning_scope import build_target_keys, compute_eligible_map
from openjiuwen.harness.personal_context.im.models import (
    ImLearningCursor,
    ImLearningMessage,
    ImLearningTarget,
)
from openjiuwen.harness.personal_context.im.source import ImLearningSource
from openjiuwen.harness.personal_context.im.normalize import normalize_batch
from openjiuwen.harness.personal_context.im.persist import persist_batch
from openjiuwen.harness.personal_context.im.schema import init_im_schema

im_logger = LogManager.get_logger("im_learning")

MAX_PAGES = 10


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class TargetFetchOutcome:
    """Result of fetching one target in one cycle."""

    target: ImLearningTarget
    mode: str  # 'backfill' | 'steady'
    messages_persisted: int = 0
    pages: int = 0
    truncated: bool = False
    error: str | None = None


@dataclass
class ImCorpusSink:
    """Persistence sink over a dedicated ``im_context.db`` connection."""

    conn: object  # sqlite3.Connection; typed as object to keep the boundary thin

    def persist(
        self,
        *,
        target: ImLearningTarget,
        messages: tuple[ImLearningMessage, ...],
        eligible_map: dict[str, int],
        fetched_at_ms: int,
    ) -> int:
        batch = normalize_batch(
            target=target,
            messages=list(messages),
            fetched_at_ms=fetched_at_ms,
            learning_eligible_map=eligible_map,
        )
        result = persist_batch(self.conn, batch)  # type: ignore[arg-type]
        return result.messages_upserted


class ImLearningFetchProvider:
    """Walk the learning whitelist and pull messages for every target."""

    def __init__(
        self,
        *,
        source: ImLearningSource,
        conn,  # sqlite3.Connection to im_context.db
        targets: tuple[ImLearningTarget, ...],
        since_ms: int | None,
        fetch_top_n: int = 50,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self._source = source
        self._conn = conn
        self._targets = tuple(targets)
        self._since_ms = since_ms
        self._fetch_top_n = max(1, int(fetch_top_n or 1))
        self._max_pages = max(1, int(max_pages or 1))
        self._sink = ImCorpusSink(conn=conn)
        self._whitelist_keys = build_target_keys(self._targets)

    async def run_once(self, *, now_ms: Optional[int] = None) -> tuple[TargetFetchOutcome, ...]:
        """Fetch every whitelist target once; per-target failures do not block others."""
        await asyncio.to_thread(init_im_schema, self._conn)
        outcomes: list[TargetFetchOutcome] = []
        for target in self._targets:
            try:
                outcome = await self.fetch_target(target, now_ms=now_ms)
            except Exception as exc:  # noqa: BLE001
                im_logger.exception(
                    "im.learning.fetch.target_failed channel=%s kind=%s external_id=%s",
                    target.channel_id,
                    target.kind,
                    target.external_id,
                )
                outcome = TargetFetchOutcome(
                    target=target,
                    mode="unknown",
                    error=str(exc),
                )
            outcomes.append(outcome)
        return tuple(outcomes)

    async def fetch_target(self, target: ImLearningTarget, *, now_ms: Optional[int] = None) -> TargetFetchOutcome:
        """Fetch one target in one cycle (public: scheduler wraps it in a lease run)."""
        ts = int(now_ms if now_ms is not None else _now_ms())
        state = await asyncio.to_thread(read_backfill_state, self._conn, target=target)
        if state is None or state.status != "complete":
            return await self._fetch_backfill(target, state=state, now_ms=ts)
        return await self._fetch_steady(target, state=state, now_ms=ts)

    async def _fetch_backfill(
        self,
        target: ImLearningTarget,
        *,
        state: Optional[BackfillState],
        now_ms: int,
    ) -> TargetFetchOutcome:
        """Backfill mode: cover history down to ``since_ms`` (or platform end)."""
        floor_ms = self._since_ms if self._since_ms is not None else 0
        result = await self._fetch_pages(
            target,
            stop_predicate=backfill_stop_predicate(floor_ms),
            start_cursor=self._resume_cursor(state),
        )
        persisted = await asyncio.to_thread(self._persist_result, target, result, now_ms=now_ms)
        oldest = result.oldest
        floor_reached = oldest is not None and int(oldest.sent_at or 0) <= floor_ms
        complete = (not result.truncated) or floor_reached
        await asyncio.to_thread(
            record_backfill_result,
            self._conn,
            target=target,
            status="complete" if complete else "truncated",
            truncated=not complete,
            oldest_sent_at_ms=int(oldest.sent_at) if oldest is not None else None,
            oldest_msg_id=oldest.msg_id if oldest is not None else None,
            newest_seen_msg_id=result.newest.msg_id if result.newest is not None else None,
            newest_seen_sent_at=int(result.newest.sent_at) if result.newest is not None else None,
            now_ms=now_ms,
        )
        return TargetFetchOutcome(
            target=target,
            mode="backfill",
            messages_persisted=persisted,
            pages=result.pages,
            truncated=not complete,
        )

    async def _fetch_steady(
        self,
        target: ImLearningTarget,
        *,
        state: BackfillState,
        now_ms: int,
    ) -> TargetFetchOutcome:
        """Steady mode (D3): page down from newest until the newest_seen boundary."""
        result = await self._fetch_pages(
            target,
            stop_predicate=newest_seen_stop_predicate(
                newest_seen_msg_id=state.newest_seen_msg_id,
                newest_seen_sent_at=state.newest_seen_sent_at,
            ),
        )
        persisted = await asyncio.to_thread(self._persist_result, target, result, now_ms=now_ms)
        newest = result.newest
        if newest is not None:
            # Advance the watermark only after the persist commit succeeded.
            await asyncio.to_thread(
                record_backfill_result,
                self._conn,
                target=target,
                status="complete",
                truncated=False,
                oldest_sent_at_ms=None,
                oldest_msg_id=None,
                newest_seen_msg_id=newest.msg_id,
                newest_seen_sent_at=int(newest.sent_at),
                now_ms=now_ms,
            )
        return TargetFetchOutcome(
            target=target,
            mode="steady",
            messages_persisted=persisted,
            pages=result.pages,
            truncated=result.truncated,
        )

    async def _fetch_pages(
        self,
        target: ImLearningTarget,
        *,
        stop_predicate,
        start_cursor: ImLearningCursor | None = None,
    ) -> FetchPageRangeResult:
        """Page range with an optional resume cursor (backfill continuation)."""
        return await fetch_page_range(
            self._source,
            target,
            stop_predicate=stop_predicate,
            max_pages=self._max_pages,
            count=self._fetch_top_n,
            start_cursor=start_cursor,
        )

    def _persist_result(
        self,
        target: ImLearningTarget,
        result: FetchPageRangeResult,
        *,
        now_ms: int,
    ) -> int:
        if not result.messages:
            return 0
        eligible_map = compute_eligible_map(
            target=target,
            messages=result.messages,
            whitelist_keys=self._whitelist_keys,
            since_ms=self._since_ms,
        )
        return self._sink.persist(
            target=target,
            messages=result.messages,
            eligible_map=eligible_map,
            fetched_at_ms=now_ms,
        )

    def _resume_cursor(self, state: Optional[BackfillState]) -> ImLearningCursor | None:
        """Resume cursor for a truncated backfill: continue from the oldest msg."""
        if state is None or state.status != "truncated":
            return None
        if not state.oldest_msg_id:
            return None
        return ImLearningCursor(message_id=state.oldest_msg_id, query_direction=0, count=self._fetch_top_n)


__all__ = ["ImCorpusSink", "ImLearningFetchProvider", "TargetFetchOutcome"]
