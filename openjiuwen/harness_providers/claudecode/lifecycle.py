# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Turn boundaries taken from the Claude CLI's delivery receipts.

The CLI acknowledges every user message it is handed with ``command_lifecycle``
frames -- ``queued``, ``started``, ``completed`` -- keyed by the ``uuid`` the
message carried.  The SDK message parser drops those frames, so they are read
one layer below it: :class:`LifecycleTap` wraps the transport, records the id
of every message written out, follows the receipts coming back, and appends a
synthetic ``system`` frame once every message the turn submitted has been
answered.

That sentinel is what ends a turn.  The SDK's ``receive_response()`` cannot:
it stops at the first ``ResultMessage``, while a message steered into an idle
CLI is answered in a *new* cycle carrying a result of its own.  Ending there
would leave that cycle's output outside the turn, to be consumed by the next
one.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from enum import StrEnum
from typing import Any

from openjiuwen.harness_providers.base import logger

# Subtype of the synthetic ``system`` frame appended when a turn settles. Any
# unknown system subtype parses into a plain ``SystemMessage``, which is how
# the signal survives the SDK parser and reaches the turn loop.
SETTLED_SUBTYPE = "openjiuwen.turn.settled"
LIFECYCLE_FRAME_TYPE = "command_lifecycle"


class CommandState(StrEnum):
    """How far the CLI has taken one submitted message."""

    SUBMITTED = "submitted"
    QUEUED = "queued"
    STARTED = "started"


class TurnCycleTracker:
    """Decide when a Claude turn is over, from the CLI's delivery receipts.

    A turn is over once a result has arrived and no message it submitted is
    still outstanding.  That is exactly what separates a steered message the
    CLI folds into the running cycle (one result for both messages) from one
    it answers as a new cycle (a result of its own).
    """

    def __init__(self, *, ack_timeout_s: float) -> None:
        """Bind how long a message may go unacknowledged before it is dropped.

        Args:
            ack_timeout_s: Seconds to wait, after a result, for a receipt on a
                message the CLI has not acknowledged at all.
        """
        self._ack_timeout_s = max(0.0, ack_timeout_s)
        self._turn_id: str | None = None
        self._outstanding: dict[str, CommandState] = {}
        self._results = 0
        self._settled = True
        # Session-wide: a CLI build that reports receipts at all reports them
        # for the first message, well before the first result.
        self._lifecycle_seen = False
        self._ack_deadline: float | None = None
        self._diagnostics: list[str] = []

    @property
    def lifecycle_seen(self) -> bool:
        """Return whether the CLI has ever reported a delivery receipt."""
        return self._lifecycle_seen

    def begin_turn(self, turn_id: str) -> None:
        """Start tracking the messages submitted for ``turn_id``."""
        self._turn_id = turn_id
        self._outstanding = {}
        self._results = 0
        self._settled = False
        self._ack_deadline = None
        self._diagnostics = []

    def end_turn(self) -> None:
        """Stop tracking; frames arriving between turns are ignored."""
        self._turn_id = None
        self._settled = True
        self._outstanding = {}
        self._ack_deadline = None

    def drain_diagnostics(self) -> tuple[str, ...]:
        """Return and clear what went wrong while settling the turn."""
        reported = tuple(self._diagnostics)
        self._diagnostics = []
        return reported

    def note_outbound(self, data: str) -> None:
        """Record the id of a user message written to the CLI."""
        if self._turn_id is None or self._settled:
            return
        frame = _decode(data)
        if frame is None or frame.get("type") != "user":
            return
        if frame.get("parent_tool_use_id"):
            # Sub-agent traffic; the CLI answers it inside a message of ours.
            return
        message_id = frame.get("uuid")
        if isinstance(message_id, str) and message_id:
            self._outstanding[message_id] = CommandState.SUBMITTED

    def note_inbound(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        """Follow one inbound frame; return the sentinel once the turn settles."""
        if self._turn_id is None or self._settled:
            return None
        kind = frame.get("type")
        if kind == LIFECYCLE_FRAME_TYPE:
            self._lifecycle_seen = True
            self._note_receipt(frame)
        elif kind == "result":
            self._results += 1
            if frame.get("subtype") != "success" or frame.get("is_error"):
                # An interrupted or failed cycle stops the CLI from working
                # its queue: the receipts still outstanding never arrive.
                self._abandon("ended the turn before answering")
        else:
            return None
        return self._settle_if_ready()

    def abandon_unacknowledged(self) -> dict[str, Any] | None:
        """Settle a turn whose last messages never drew a receipt."""
        if self._turn_id is None or self._settled:
            return None
        self._abandon("never acknowledged")
        return self._settle_if_ready()

    def straggler_timeout(self) -> float | None:
        """Return how long to wait for a receipt that has not arrived yet."""
        if self._ack_deadline is None:
            return None
        return max(0.0, self._ack_deadline - time.monotonic())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _note_receipt(self, frame: Mapping[str, Any]) -> None:
        message_id = frame.get("command_uuid")
        if not isinstance(message_id, str) or message_id not in self._outstanding:
            return
        state = frame.get("state")
        if state == "completed":
            del self._outstanding[message_id]
        elif state == "queued":
            self._outstanding[message_id] = CommandState.QUEUED
        elif state == "started":
            self._outstanding[message_id] = CommandState.STARTED

    def _abandon(self, reason: str) -> None:
        if not self._outstanding:
            return
        self._diagnostics.append(f"{reason} {len(self._outstanding)} message(s): {', '.join(sorted(self._outstanding))}")
        self._outstanding = {}
        self._ack_deadline = None

    def _settle_if_ready(self) -> dict[str, Any] | None:
        if self._results < 1:
            return None
        if not self._lifecycle_seen:
            # A CLI build that reports no receipts leaves the first result as
            # the only turn boundary there is.
            self._outstanding = {}
            return self._sentinel()
        if self._outstanding:
            self._arm_ack_deadline()
            return None
        self._ack_deadline = None
        return self._sentinel()

    def _arm_ack_deadline(self) -> None:
        working = any(state is not CommandState.SUBMITTED for state in self._outstanding.values())
        if working:
            # The CLI took the message; however long the answer runs, it is
            # this turn's work and must not be timed out.
            self._ack_deadline = None
            return
        if self._ack_deadline is None:
            self._ack_deadline = time.monotonic() + self._ack_timeout_s

    def _sentinel(self) -> dict[str, Any]:
        self._settled = True
        return {
            "type": "system",
            "subtype": SETTLED_SUBTYPE,
            "turn_id": self._turn_id,
            "results": self._results,
        }


class LifecycleTap:
    """Wrap an SDK transport to read receipts and mark the end of a turn.

    Every real frame is forwarded unchanged and in order; the only frame this
    adds is the settled sentinel, right after the frame that settles the turn.
    """

    def __init__(self, inner: Any, tracker: TurnCycleTracker) -> None:
        """Wrap ``inner``, reporting its receipts to ``tracker``."""
        self._inner = inner
        self._tracker = tracker

    @property
    def inner(self) -> Any:
        """Return the wrapped transport."""
        return self._inner

    async def connect(self) -> None:
        """Connect the wrapped transport."""
        await self._inner.connect()

    async def write(self, data: str) -> None:
        """Record the id of an outbound message, then write it unchanged."""
        self._tracker.note_outbound(data)
        await self._inner.write(data)

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Forward every frame, appending the sentinel that ends a turn."""
        # The frames are drained in a task of their own so that waiting for a
        # missing receipt can time out: cancelling a read straight off the
        # transport would leave its async generator unusable, while cancelling
        # a queue read loses nothing.
        queue: asyncio.Queue[tuple[dict[str, Any] | None, Exception | None]] = asyncio.Queue()
        drain = asyncio.create_task(self._drain(queue), name="claude_lifecycle_tap")
        try:
            while True:
                item = await self._next(queue)
                if item is None:
                    sentinel = self._tracker.abandon_unacknowledged()
                    if sentinel is not None:
                        logger.debug("[claude-code] settling the turn without every delivery receipt")
                        yield sentinel
                    continue
                frame, failure = item
                if failure is not None:
                    raise failure
                if frame is None:
                    return
                yield frame
                sentinel = self._tracker.note_inbound(frame)
                if sentinel is not None:
                    yield sentinel
        finally:
            drain.cancel()

    async def close(self) -> None:
        """Close the wrapped transport."""
        await self._inner.close()

    async def end_input(self) -> None:
        """End the wrapped transport's input stream."""
        await self._inner.end_input()

    def is_ready(self) -> bool:
        """Return whether the wrapped transport is ready."""
        return bool(self._inner.is_ready())

    async def _next(
        self,
        queue: asyncio.Queue[tuple[dict[str, Any] | None, Exception | None]],
    ) -> tuple[dict[str, Any] | None, Exception | None] | None:
        """Take the next drained item, or ``None`` when a receipt timed out."""
        timeout = self._tracker.straggler_timeout()
        if timeout is None:
            return await queue.get()
        try:
            return await asyncio.wait_for(queue.get(), timeout)
        except TimeoutError:
            return None

    async def _drain(
        self,
        queue: asyncio.Queue[tuple[dict[str, Any] | None, Exception | None]],
    ) -> None:
        """Move transport frames onto ``queue``, ending with a ``None`` frame."""
        try:
            async for frame in self._inner.read_messages():
                queue.put_nowait((frame, None))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Raised again on the consumer side, so the SDK sees the transport
            # failure exactly as it would without the tap.
            queue.put_nowait((None, exc))
        else:
            queue.put_nowait((None, None))


def _decode(data: str) -> Mapping[str, Any] | None:
    """Decode one outbound NDJSON line, ignoring anything unparsable."""
    try:
        frame = json.loads(data)
    except ValueError:
        return None
    return frame if isinstance(frame, Mapping) else None
