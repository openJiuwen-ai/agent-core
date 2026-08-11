# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Control events for NativeHarness supervisor.

External API methods push ControlEvent instances onto an asyncio.Queue;
the supervisor coroutine consumes them serially. Acks are returned via
asyncio.Future when external callers need confirmation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Union

from openjiuwen.agent_teams.harness.state import InboxMessage


@dataclass(frozen=True, slots=True)
class _CmdSend:
    """A send() invocation reaching the supervisor.

    Attributes:
        msg: Wrapped inbound message.
        ack: Future resolved with the message seq id.
    """

    msg: InboxMessage
    ack: asyncio.Future


@dataclass(frozen=True, slots=True)
class _CmdAbort:
    """An abort() invocation reaching the supervisor.

    Attributes:
        immediate: True for cancel+rollback; False for iteration-granular
            graceful abort.
        ack: Future resolved with None after the supervisor has applied
            the abort intent (graceful flag set, or rollback finished).
    """

    immediate: bool
    ack: asyncio.Future


@dataclass(frozen=True, slots=True)
class _CmdPause:
    """A pause() invocation reaching the supervisor.

    Attributes:
        ack: Future resolved with None once the harness has settled to PAUSED.
            For an LLM-phase pause this is synchronous; for a tool-phase pause
            it is deferred until the current iteration completes cooperatively
            (the supervisor stashes it on ``ActiveRound.pause_ack``).
    """

    ack: asyncio.Future


@dataclass(frozen=True, slots=True)
class _CmdRoundFinished:
    """Internal notification emitted by the round task when it finishes
    (success, cancellation, or error).

    Attributes:
        round_id: Id of the finished round.
        error: Exception raised by the round, or None on success.
        result: The round result dict from ``wait_round_completion``, used by
            ``_on_round_done`` to drive coordinator + multi-round decisions.
            None on cancellation / error.
    """

    round_id: int
    error: BaseException | None
    result: dict | None = None


@dataclass(frozen=True, slots=True)
class _CmdStop:
    """A stop() invocation reaching the supervisor.

    Attributes:
        ack: Future resolved with None after the supervisor has cancelled
            any active round and closed the output queue with a sentinel.
    """

    ack: asyncio.Future


@dataclass(frozen=True, slots=True)
class _CmdResume:
    """A resume() invocation reaching the supervisor.

    Attributes:
        ack: Future resolved with None after the supervisor has started a
            continuation round from the paused round's preserved context.
        query: Cold-resume payload. When the harness was stopped and rebuilt,
            its context comes back from the session checkpoint and the paused
            round's originating query is supplied here. ``None`` for a warm
            resume, which reads the query the pause cached in memory.
    """

    ack: asyncio.Future
    query: str | None = None


@dataclass(frozen=True, slots=True)
class _CmdSteer:
    """A steer() invocation reaching the supervisor.

    Distinct from ``_CmdSend`` with ``immediate=True`` because the two want
    opposite things from an idle harness. ``send`` starts a round; steering has
    no meaning without a round already running and must say so instead.

    The phase can only be read safely here. A caller that checks
    ``active_round`` and then calls ``send`` has an await between the two, and
    the round it saw may have finished by the time the supervisor acts on the
    message -- turning a correction for a finished round into a brand new one.

    Attributes:
        content: Steering text to inject into the running round.
        ack: Future resolved True when the text was pushed into the active
            round's steering queue, False when there was no round to steer.
        steer_id: Correlation id for the request that produced this steer.
            Carried so ``STEER_APPLIED`` can name which steer a rail dropped;
            its ``dropped`` list is built from these ids and skips ``None``, so
            without one a dropped steer is silently reported as applied.
        expected_round_id: The round the caller believed was running. Present
            because "a round is running" is not the invariant that matters --
            *which* round is. ``_CmdRoundFinished`` shares this control queue and
            ``_on_round_done`` can start the next round synchronously (follow-up
            drain, abnormal-death replay, task-plan continuation), so a steer
            dequeued behind one would find a live round that the user never saw
            and inject the correction into it. ``None`` means the caller does not
            care which round receives it.
    """

    content: str
    ack: asyncio.Future
    steer_id: str | None = None
    expected_round_id: int | None = None


ControlEvent = Union[
    _CmdSend,
    _CmdSteer,
    _CmdAbort,
    _CmdPause,
    _CmdResume,
    _CmdRoundFinished,
    _CmdStop,
]
