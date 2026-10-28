# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Numbers one Execution's events, records them in order and queues them for display."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

from ..schema.events import EVENT_MAX_BYTES, AgentEvent, EventType
from ..schema.execution import ExecutionContext
from ..schema.protocol import TEXT_MAX_CHARS, InvocationRef
from .control import ExecutionControl

CRITICAL_EVENTS = frozenset({EventType.TOOL_INTENT, EventType.EXECUTION_PAUSED, EventType.EXECUTION_FINAL})
_VISIBILITY: dict[EventType, Literal["progress", "candidate"]] = {
    EventType.REPLY_DELTA: "progress",
    EventType.PROGRESS_UPDATED: "progress",
    EventType.OUTPUT_PROPOSED: "candidate",
}
# Wider than any real sequence number, and the longest form of a UTC time.
_WIDEST_SEQ = 10**18
_WIDEST_TIME = datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
# The longest identifier, in characters of the most UTF-8 bytes; stands in for per-Execution identity.
_WIDEST_ID = "\U0001f600" * 512


def escaped_bytes(text: str) -> int:
    """UTF-8 bytes a string value adds to canonical event JSON, quotes excluded."""
    # Invalid surrogates are still sized; building the real event then rejects them as a field error.
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8", "surrogatepass")) - 2


def widest_size(execution: ExecutionContext, event_type: EventType, payload: Any, *, any_execution: bool) -> int:
    """Bytes of this event at the widest sequence number and time.

    With ``any_execution`` the Execution's own identity fields are widest too, so every Execution of
    the Request gets the same size for the same payload.
    """
    identity = InvocationRef.from_execution(execution).model_dump()
    if any_execution:
        identity.update(execution_id=_WIDEST_ID, source_ref=_WIDEST_ID)
    probe = AgentEvent(
        **identity,
        event_type=event_type,
        local_seq=_WIDEST_SEQ,
        occurred_at=_WIDEST_TIME,
        visibility=_VISIBILITY.get(event_type, "private"),
        payload=payload,
    )
    return probe.proposal_size()


def longest_prefix(text: str, room: int) -> int:
    """Characters of the longest prefix within both limits; at least one, so splitting always advances."""
    high = min(len(text), TEXT_MAX_CHARS)
    if escaped_bytes(text[:high]) <= room:
        return high
    low = 1
    while low < high:
        middle = (low + high + 1) // 2
        if escaped_bytes(text[:middle]) <= room:
            low = middle
        else:
            high = middle - 1
    return low


class EventEmitter:
    """The only record() caller of an Execution; display order equals record order."""

    def __init__(self, execution: ExecutionContext, commit, control: ExecutionControl):
        self._execution = execution
        self._commit = commit
        self._control = control
        self._seq = 0
        self._lock = asyncio.Lock()
        self.outbox: asyncio.Queue[AgentEvent] = asyncio.Queue()

    async def emit(self, event_type: EventType, payload: Any) -> AgentEvent:
        """Record one event; critical failures stop the Execution, ordinary ones are diagnosed."""
        async with self._lock:
            return await self._record(self._create(event_type, payload, datetime.now(timezone.utc)))

    async def emit_text(self, event_type: EventType, text: str) -> None:
        """Record text as consecutive events within both limits; the pieces join back to the original."""
        async with self._lock:
            rest = text
            while rest:
                occurred_at = datetime.now(timezone.utc)
                size = longest_prefix(rest, self._room(event_type, {"text": rest}, occurred_at))
                await self._record(self._create(event_type, {"text": rest[:size]}, occurred_at))
                rest = rest[size:]

    async def emit_ending(
        self, event_type: EventType, payload: Any, proposals: tuple[AgentEvent, ...] = ()
    ) -> AgentEvent:
        """Record the paused or final event; after a first failure, hand its outputs in again once and retry.

        The outputs and the ending are recorded again as the same event objects, so the host
        deduplicates them by event_id. A second failure of the ending stops the Execution.
        """
        async with self._lock:
            event = self._create(event_type, payload, datetime.now(timezone.utc))
            self._seq = event.local_seq
            try:
                await self._commit.record(self._execution, event)
            except Exception as exc:  # noqa: BLE001 - any record failure gets the one retry
                logger.warning(
                    "[PersistentAgent] ending record failed, retrying once: execution=%s seq=%s type=%s error=%s",
                    self._execution.execution_id,
                    event.local_seq,
                    event.event_type,
                    exc,
                )
                await self._retry_ending(event, proposals)
            self.outbox.put_nowait(event)
            return event

    async def _retry_ending(self, event: AgentEvent, proposals: tuple[AgentEvent, ...]) -> None:
        for proposal in proposals:
            try:
                await self._commit.record(self._execution, proposal)
            except Exception as exc:  # noqa: BLE001 - the ending retry decides whether the handoff holds
                logger.warning(
                    "[PersistentAgent] output re-record failed: execution=%s seq=%s error=%s",
                    self._execution.execution_id,
                    proposal.local_seq,
                    exc,
                )
        try:
            await self._commit.record(self._execution, event)
        except Exception as exc:
            error = build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                cause=exc,
                error_msg=f"critical record of {event.event_type} failed",
            )
            self._control.mark_fatal(error)
            raise error from exc

    def _room(self, event_type: EventType, payload: dict, occurred_at: datetime) -> int:
        """Bytes left for the text field once every other field of the next event is counted."""
        probe = self._create(event_type, {**payload, "text": "x"}, occurred_at)
        return EVENT_MAX_BYTES - (probe.proposal_size() - 1)

    def _create(self, event_type: EventType, payload: Any, occurred_at: datetime) -> AgentEvent:
        """Build the next event without numbering it; only a recorded event uses its sequence number."""
        return AgentEvent.create(
            self._execution,
            event_type=event_type,
            local_seq=self._seq + 1,
            occurred_at=occurred_at,
            visibility=_VISIBILITY.get(event_type, "private"),
            payload=payload,
        )

    async def _record(self, event: AgentEvent) -> AgentEvent:
        self._seq = event.local_seq
        try:
            await self._commit.record(self._execution, event)
        except Exception as exc:
            if event.event_type in CRITICAL_EVENTS:
                error = build_error(
                    StatusCode.DEEPAGENT_RUNTIME_ERROR,
                    cause=exc,
                    error_msg=f"critical record of {event.event_type} failed",
                )
                self._control.mark_fatal(error)
                raise error from exc
            logger.warning(
                "[PersistentAgent] ordinary event record failed: execution=%s seq=%s type=%s error=%s",
                self._execution.execution_id,
                event.local_seq,
                event.event_type,
                exc,
            )
        self.outbox.put_nowait(event)
        return event
