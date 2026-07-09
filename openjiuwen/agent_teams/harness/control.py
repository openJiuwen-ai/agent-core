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
    """

    ack: asyncio.Future


ControlEvent = Union[
    _CmdSend,
    _CmdAbort,
    _CmdPause,
    _CmdResume,
    _CmdRoundFinished,
    _CmdStop,
]
