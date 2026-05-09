# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Interaction gate for the Run/Interact concurrency contract.

Each :class:`ActiveTeam` in the runtime pool owns one ``InteractGate``.
While a ``run_agent_team_streaming`` call is in flight, ``interact_team``
may admit new payloads through the gate. When the run is about to exit,
it closes the gate (rejecting further interacts) and waits for any
in-flight payloads to be consumed before the stream actually finishes.

State transitions::

    OPEN    --admit()------>      OPEN, inflight++
    OPEN    --close_and_drain()-> CLOSING --(inflight==0)--> DRAINED
    CLOSING --admit()------>      None (rejected)
    *       --consume_done(t)-->  inflight--; signal drained when zero

Designed for single-event-loop use under ActiveTeam ownership; all public
methods are coroutines and serialise mutations via an internal lock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdmissionTicket:
    """Opaque token returned from a successful ``admit`` call.

    The caller must pass it back to ``consume_done`` after the agent
    has actually consumed the payload, so the gate can drain correctly.
    Tickets are only constructed inside ``InteractGate.admit``; external
    callers should treat the ``gate`` field as opaque.
    """

    gate: "InteractGate"


class InteractGate:
    """Async counter of in-flight interact payloads with a close-and-drain step."""

    def __init__(self) -> None:
        self._closed = asyncio.Event()
        self._inflight = 0
        self._drained = asyncio.Event()
        self._drained.set()
        self._lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        """Whether the gate has been closed to new admissions."""
        return self._closed.is_set()

    @property
    def inflight(self) -> int:
        """Current count of admitted-but-not-yet-consumed payloads."""
        return self._inflight

    async def admit(self) -> AdmissionTicket | None:
        """Try to admit a new payload.

        Returns ``None`` when the gate is closed; otherwise increments
        inflight and returns a ticket bound to this gate.
        """
        async with self._lock:
            if self._closed.is_set():
                return None
            self._inflight += 1
            self._drained.clear()
            return AdmissionTicket(gate=self)

    async def consume_done(self, ticket: AdmissionTicket) -> None:
        """Mark the payload identified by ``ticket`` as consumed.

        Tickets from a different gate are silently ignored.
        """
        if ticket.gate is not self:
            return
        async with self._lock:
            if self._inflight <= 0:
                return
            self._inflight -= 1
            if self._inflight == 0:
                self._drained.set()

    async def close_and_drain(self) -> None:
        """Stop admitting new payloads and wait for in-flight ones to drain."""
        async with self._lock:
            self._closed.set()
            if self._inflight == 0:
                self._drained.set()
                return
        await self._drained.wait()

    async def reset(self) -> None:
        """Reopen the gate for a fresh run cycle.

        Clears the ``closed`` flag and resets ``inflight`` to zero so the
        next ``run_agent_team_streaming`` admits payloads again. Tickets
        from the previous cycle are no longer trackable; callers must not
        keep references to them across a ``reset``.
        """
        async with self._lock:
            self._closed.clear()
            self._inflight = 0
            self._drained.set()


__all__ = ["AdmissionTicket", "InteractGate"]
