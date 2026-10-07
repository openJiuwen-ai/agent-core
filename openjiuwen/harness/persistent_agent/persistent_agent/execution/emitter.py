# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Numbers one Execution's events, records them in order and queues them for display."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

from ..schema.events import AgentEvent, EventType
from ..schema.execution import ExecutionContext
from .control import ExecutionControl

CRITICAL_EVENTS = frozenset({EventType.TOOL_INTENT, EventType.EXECUTION_PAUSED, EventType.EXECUTION_FINAL})
_VISIBILITY = {
    EventType.REPLY_DELTA: "progress",
    EventType.PROGRESS_UPDATED: "progress",
    EventType.OUTPUT_PROPOSED: "candidate",
}


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
            self._seq += 1
            event = AgentEvent.create(
                self._execution,
                event_type=event_type,
                local_seq=self._seq,
                occurred_at=datetime.now(timezone.utc),
                visibility=_VISIBILITY.get(event_type, "private"),
                payload=payload,
            )
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
