# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavioral protocol for third-party agent harness integrations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from openjiuwen.harness_protocol.checkpoints import HarnessCheckpoint
from openjiuwen.harness_protocol.events import EventBufferConfig
from openjiuwen.harness_protocol.models import (
    AbortMode,
    DeliveryMode,
    HarnessCard,
    HarnessContext,
    HarnessInput,
    JsonObject,
    SendReceipt,
)
from openjiuwen.harness_protocol.state import HarnessState
from openjiuwen.harness_protocol.stream import HarnessEventCursor


@runtime_checkable
class HarnessProtocol(Protocol):
    """Concurrent-safe harness behavior required of a third-party agent.

    ``events`` and ``turn_events`` are alternative views over one logical
    single-consumer observation channel. Implementations must serialize state
    transitions internally; callers may issue commands from different tasks.
    """

    @property
    def card(self) -> HarnessCard:
        """Return static implementation identity and declared capabilities."""
        ...

    @property
    def state(self) -> HarnessState:
        """Return the current high-level lifecycle state."""
        ...

    @property
    def provider_session_id(self) -> str | None:
        """Return the provider-native session id once one is available."""
        ...

    @property
    def event_buffer_config(self) -> EventBufferConfig:
        """Return the bounded backpressure policy used for observations."""
        ...

    async def start(self, context: HarnessContext) -> None:
        """Validate host compatibility, start a cycle, and settle in IDLE."""
        ...

    async def stop(self) -> None:
        """Stop the cycle, close events, and settle in ``TERMINATED``.

        The operation must be idempotent.
        """
        ...

    def events(self) -> HarnessEventCursor:
        """Return the cycle-long ordered observation stream.

        The iterator remains open across turn boundaries and ends only when
        the current ``start``/``stop`` cycle closes. Implementations must raise
        ``HarnessStateError`` if another observation iterator is
        already active.
        """
        ...

    def turn_events(self, turn_id: str | None = None) -> HarnessEventCursor:
        """Return one finite turn from the observation stream.

        ``turn_id`` identifies and validates the next unconsumed accepted turn;
        it is not an out-of-order selector and implementations must not discard
        intervening turns while searching for it. Otherwise the iterator uses
        the next turn. It yields the start and following ordered events through
        the matching ``FINISHED``, ``ABORTED``, or ``FAILED`` event. ``PAUSED``
        and ``RESUMED`` do not end the iterator. This is a serialized
        convenience view over the same logical channel as ``events``; the two
        methods must not be consumed concurrently. Implementations must reject
        a second active iterator with ``HarnessStateError`` rather than
        racing it.
        """
        ...

    async def send(
        self,
        content: HarnessInput,
        *,
        mode: DeliveryMode = DeliveryMode.AUTO,
    ) -> SendReceipt:
        """Accept input and return its message-to-turn association.

        A queued input receives its future turn ID at acceptance. A steering
        input receives the active turn ID. The call does not wait for the turn
        to finish.
        """
        ...

    async def abort(self, *, mode: AbortMode = AbortMode.GRACEFUL) -> None:
        """Abort the active turn according to a declared capability."""
        ...

    async def pause(self) -> None:
        """Pause the active turn when ``PAUSE_RESUME`` is supported."""
        ...

    async def resume(self, *, query: HarnessInput | None = None) -> None:
        """Resume a warm or checkpoint-restored paused turn."""
        ...

    async def export_checkpoint(self) -> HarnessCheckpoint | None:
        """Return the latest versioned provider snapshot for this agent.

        Implementations should also publish recoverable checkpoints through
        ``context.checkpoint_sink`` when provider state changes materially.
        """
        ...


@runtime_checkable
class HarnessProvider(Protocol):
    """Factory SPI used to discover and construct third-party harnesses."""

    @property
    def card(self) -> HarnessCard:
        """Return metadata for harnesses created by this provider."""
        ...

    def create(self, config: JsonObject) -> HarnessProtocol:
        """Validate provider configuration and create an unstarted harness."""
        ...


__all__ = ["HarnessProtocol", "HarnessProvider"]
