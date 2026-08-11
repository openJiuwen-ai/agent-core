# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025.
# All rights reserved.
"""Dual-queue buffer for steer/follow_up messages.

Bridges EventHandler -> Executor/Loop by providing two
async-safe queues:
- steering: drained by the executor before each
  inner invoke.
- follow_up: drained by outer task loop after
  each iteration completes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class SteeringInput:
    """One queued steering message, optionally carrying its request id.

    The id exists so a host can tell the client *which* steer reached the model,
    which a queue of bare strings cannot express: several steers are drained
    together and joined into one message, and rails may drop some of them along
    the way.

    ``id`` is optional because not every steer comes from a client request --
    rails steer on their own and have nothing to correlate.
    """

    text: str
    id: Optional[str] = None

    @classmethod
    def coerce(cls, msg: "str | SteeringInput") -> "SteeringInput":
        """Accept either form, so existing callers keep working unchanged."""
        return msg if isinstance(msg, cls) else cls(text=str(msg))


@dataclass
class LoopQueues:
    """Buffer between EventHandler and Executor/Loop.

    Attributes:
        steering: Queue for steer messages, drained
            by the executor before each invoke.
        follow_up: Queue for follow-up messages,
            drained by the outer task loop.
    """

    steering: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )
    follow_up: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )

    def push_steer(self, msg: "str | SteeringInput") -> None:
        """Push a steering message.

        Args:
            msg: Steering text, or a :class:`SteeringInput` when the caller has
                an id to correlate an acknowledgement with. Coerced on push, so
                readers never have to test which form arrived.
        """
        self.steering.put_nowait(SteeringInput.coerce(msg))

    def push_follow_up(self, msg: str) -> None:
        """Push a follow-up message.

        Args:
            msg: Follow-up content text.
        """
        self.follow_up.put_nowait(msg)

    def has_follow_up(self) -> bool:
        """Return whether follow-up messages are pending."""
        return not self.follow_up.empty()

    def drain_steering(self) -> List[SteeringInput]:
        """Drain all pending steering messages.

        Returns:
            The queued inputs, oldest first. Always ``SteeringInput`` — the
            queue coerces on push.
        """
        msgs: List[SteeringInput] = []
        while not self.steering.empty():
            try:
                msgs.append(SteeringInput.coerce(self.steering.get_nowait()))
            except asyncio.QueueEmpty:
                break
        return msgs

    def drain_follow_up(self) -> List[str]:
        """Drain all pending follow-up messages.

        Returns:
            List of follow-up message strings.
        """
        msgs: List[str] = []
        while not self.follow_up.empty():
            try:
                msgs.append(self.follow_up.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs


__all__ = ["LoopQueues"]
