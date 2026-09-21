"""Cursor-driven page-range fetch for IM learning (decision D7).

The fetch loop is driven by the opaque ``ImLearningCursor``: it never
inspects whether the host connector pages by WeLink-style
``message_id + query_direction`` or by Feishu/DingTalk-style
``extra["page_token"]`` — the host-side ``ConnectorLearningSource`` adapter
already normalizes that.  The only termination contract is
``ImMessageBatch.next_cursor is None`` ("no more pages") plus a caller
supplied ``stop_predicate``.

Two stop predicates cover the two fetch modes (see migration plan §5.2):

- backfill: page down from newest until the oldest page message reaches the
  ``since_ms`` floor (or the platform runs out of pages);
- steady state (newest_seen, decision D3): page down from newest until the
  page contains the previously-seen newest message or reaches its
  ``sent_at`` — one algorithm for every platform, since Feishu/DingTalk
  page tokens can only page towards older messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from openjiuwen.harness.personal_context.im.models import (
    ImLearningCursor,
    ImLearningMessage,
    ImLearningTarget,
    ImMessageBatch,
)
from openjiuwen.harness.personal_context.im.normalize import cursor_count, newest_message, oldest_message
from openjiuwen.harness.personal_context.im.source import ImLearningSource

MAX_PAGES = 10  # 单轮最多翻 10 页，防止阻塞其他目标

StopPredicate = Callable[[ImMessageBatch], bool]


@dataclass(frozen=True)
class FetchPageRangeResult:
    """Aggregated result of one cursor-driven page-range fetch."""

    messages: tuple[ImLearningMessage, ...]
    pages: int
    truncated: bool
    newest: ImLearningMessage | None
    oldest: ImLearningMessage | None
    last_cursor: ImLearningCursor | None


def _dedup(existing: list[ImLearningMessage], incoming: tuple[ImLearningMessage, ...]) -> list[ImLearningMessage]:
    """Dedup by (channel_id, msg_id); page boundaries may overlap."""
    seen = {(m.channel_id, m.msg_id) for m in existing if m.msg_id}
    appended: list[ImLearningMessage] = []
    for msg in incoming:
        key = (msg.channel_id, msg.msg_id)
        if not msg.msg_id or key in seen:
            continue
        seen.add(key)
        appended.append(msg)
    return appended


def backfill_stop_predicate(floor_ms: int) -> StopPredicate:
    """Stop when the page's oldest message reaches the ``since_ms`` floor."""

    def _stop(batch: ImMessageBatch) -> bool:
        oldest = oldest_message(batch.messages)
        return oldest is not None and int(oldest.sent_at or 0) <= floor_ms

    return _stop


def newest_seen_stop_predicate(
    *,
    newest_seen_msg_id: str | None,
    newest_seen_sent_at: int | None,
) -> StopPredicate:
    """Stop when the page contains the previously-seen newest message.

    Two signals, either suffices:
    - an exact ``msg_id`` match (anchor still alive);
    - the page's oldest ``sent_at`` has reached the watermark (anchor
      deleted/recalled — fall back to time-based reconciliation; anything
      missed is recovered by the next cycle from the new boundary).
    """

    def _stop(batch: ImMessageBatch) -> bool:
        for msg in batch.messages:
            if newest_seen_msg_id and msg.msg_id == newest_seen_msg_id:
                return True
        oldest = oldest_message(batch.messages)
        if oldest is not None and newest_seen_sent_at is not None:
            if int(oldest.sent_at or 0) <= int(newest_seen_sent_at):
                return True
        return False

    return _stop


async def fetch_page_range(
    source: ImLearningSource,
    target: ImLearningTarget,
    *,
    stop_predicate: StopPredicate | None = None,
    max_pages: int = MAX_PAGES,
    count: int = 50,
    start_cursor: ImLearningCursor | None = None,
) -> FetchPageRangeResult:
    """Fetch pages starting from the newest, driven by the opaque cursor.

    ``cursor=None`` starts from the newest page on every platform;
    ``start_cursor`` resumes a truncated range (e.g. a backfill continuation
    anchored at the saved oldest msg_id).  Paging continues while the platform
    offers ``next_cursor`` and the caller's ``stop_predicate`` has not fired;
    hitting ``max_pages`` marks the result truncated (the steady-state
    algorithm resumes from the top next cycle).
    """
    cursor: ImLearningCursor | None = start_cursor
    messages: list[ImLearningMessage] = []
    pages = 0
    truncated = False
    for _ in range(max(1, max_pages)):
        batch = await source.fetch_messages(
            target,
            ImLearningCursor(
                message_id=cursor.message_id if cursor else None,
                query_direction=cursor.query_direction if cursor else None,
                count=cursor_count(cursor, default=count),
                extra=dict(cursor.extra) if cursor else {},
            )
            if cursor is not None
            else None,
        )
        pages += 1
        appended = _dedup(messages, batch.messages)
        messages.extend(appended)
        if batch.next_cursor is None:
            # Platform declares no more pages.
            break
        if not appended and not batch.messages:
            # Empty page with a next cursor: treat as exhausted to avoid
            # spinning on platforms that always return a token.
            break
        if stop_predicate is not None and stop_predicate(batch):
            break
        cursor = batch.next_cursor
    else:
        # Loop exhausted max_pages without a natural stop.
        truncated = True
    return FetchPageRangeResult(
        messages=tuple(messages),
        pages=pages,
        truncated=truncated,
        newest=newest_message(messages),
        oldest=oldest_message(messages),
        last_cursor=cursor,
    )


__all__ = [
    "FetchPageRangeResult",
    "MAX_PAGES",
    "StopPredicate",
    "backfill_stop_predicate",
    "fetch_page_range",
    "newest_seen_stop_predicate",
]
