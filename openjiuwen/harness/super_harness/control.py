# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Control events for SuperHarness supervisor.

External API methods push ControlEvent instances onto an asyncio.Queue;
the supervisor coroutine consumes them serially. Acks are returned via
asyncio.Future when external callers need confirmation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Union

from openjiuwen.harness.super_harness.state import InboxMessage


@dataclass(frozen=True, slots=True)
class _Cmd_Send:
    """A send() invocation reaching the supervisor.

    Attributes:
        msg: Wrapped inbound message.
        ack: Future resolved with the message seq id.
    """

    msg: InboxMessage
    ack: asyncio.Future


@dataclass(frozen=True, slots=True)
class _Cmd_Abort:
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
class _Cmd_Pause:
    """A pause() invocation reaching the supervisor.

    Attributes:
        ack: Future resolved with None after the supervisor has cancelled
            the active round, rolled back state, and cached paused_query.
    """

    ack: asyncio.Future


@dataclass(frozen=True, slots=True)
class _Cmd_RoundFinished:
    """Internal notification emitted by the round forwarder when a round
    finishes (success, cancellation, or error).

    Attributes:
        round_id: Id of the finished round.
        error: Exception raised by the round, or None on success.
    """

    round_id: int
    error: BaseException | None


@dataclass(frozen=True, slots=True)
class _Cmd_Stop:
    """A stop() invocation reaching the supervisor.

    Attributes:
        ack: Future resolved with None after the supervisor has cancelled
            any active round and closed the output queue with a sentinel.
    """

    ack: asyncio.Future


ControlEvent = Union[
    _Cmd_Send,
    _Cmd_Abort,
    _Cmd_Pause,
    _Cmd_RoundFinished,
    _Cmd_Stop,
]
