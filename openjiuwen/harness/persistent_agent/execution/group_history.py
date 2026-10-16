# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Current-group background with bounded queued-trigger backfill, independent of PA-07."""

from typing import TYPE_CHECKING

from ..schema.context import GroupMessagesResult
from ..schema.protocol import InvocationRef

if TYPE_CHECKING:
    from ..schema.build_context import PersistentBuildContext

MESSAGE_LIMIT = 500
BACKFILL_PAGE_LIMIT = 32


async def _page(context: "PersistentBuildContext", cursor: str | None) -> GroupMessagesResult:
    result = GroupMessagesResult.model_validate(
        await context.ports.group_chat.recent_messages(context.execution, cursor=cursor, limit=MESSAGE_LIMIT)
    )
    result.assert_matches(context.execution)
    return result


async def _backfill(context, trigger: str, boundary: str, known_ids: set[str]):
    """Read forward to a fixed trusted position; never merge an unproven overshoot."""
    cursor = trigger
    visited = set()
    messages = {}
    known_updates = {}
    incomplete = False
    for _ in range(BACKFILL_PAGE_LIMIT):
        if cursor in visited:
            break
        visited.add(cursor)
        page = await _page(context, cursor)
        incomplete |= page.status == "incomplete"
        reached = False
        for message in page.messages:
            messages[message.message_id] = message
            if message.message_id in known_ids:
                known_updates[message.message_id] = message
            if message.message_id == boundary:
                reached = True
                break
        if len(messages) > MESSAGE_LIMIT:
            incomplete = True
            messages = dict(list(messages.items())[-MESSAGE_LIMIT:])
        if reached or page.scanned_through == boundary:
            return {**messages, **known_updates}, incomplete
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    # A newer scan watermark does not prove that the original boundary was covered.
    # Already-selected IDs remain in range even if the backfill cannot prove its whole interval.
    # Keep their newer observations so an interrupted scan cannot resurrect a recalled body.
    return known_updates, True


class ExecutionGroupMessageReader:
    """Read delta and queued-request background without changing the host's scan watermark."""

    async def recent_messages(
        self, context: "PersistentBuildContext", *, after: str | None = None, trigger_message_id: str | None = None
    ) -> GroupMessagesResult:
        delta = await _page(context, after)
        messages = {message.message_id: message for message in delta.messages}
        incomplete = delta.status == "incomplete" or delta.next_cursor is not None
        boundary = delta.scanned_through
        if trigger_message_id is not None and trigger_message_id not in (after, boundary):
            if boundary is None:
                incomplete = True
            else:
                backfill, partial = await _backfill(context, trigger_message_id, boundary, set(messages))
                # Later observations win, including recalled/unavailable bodies.
                messages.update(backfill)
                incomplete |= partial
        messages.pop(trigger_message_id, None)
        ordered = sorted(messages.values(), key=lambda message: (message.received_at, message.message_id))
        if len(ordered) > MESSAGE_LIMIT:
            incomplete = True
            ordered = ordered[-MESSAGE_LIMIT:]
        return GroupMessagesResult(
            **InvocationRef.from_execution(context.execution).model_dump(),
            status="incomplete" if incomplete else "found" if ordered else "empty",
            messages=ordered,
            next_cursor=delta.next_cursor,
            scanned_through=boundary,
            reason=(delta.reason or "群背景扫描或触发消息补读未完成，不能据此判断没有其他消息。")
            if incomplete
            else None,
        )
