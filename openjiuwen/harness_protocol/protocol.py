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
    ModelChoice,
    ModelOption,
    ModelSelection,
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
class HarnessModelControl(Protocol):
    """Optional model control a harness may implement next to ``HarnessProtocol``.

    It is a separate Protocol so existing ``HarnessProtocol`` implementations
    stay conformant. Hosts check ``card.supports(MODEL_DISCOVERY)`` /
    ``card.supports(MODEL_SELECTION)`` before calling; an implementation that
    does not declare the capability raises ``UnsupportedHarnessCapabilityError``.
    """

    async def list_models(self) -> tuple[ModelOption, ...]:
        """Probe the models the provider currently offers.

        Works on a started harness (reads the live session) and on an
        unstarted one (performs a short handshake from provider configuration
        without issuing a model request). Requires ``MODEL_DISCOVERY``.
        """
        ...

    async def set_model(self, selection: ModelSelection) -> None:
        """Switch model and/or reasoning effort for the following turns.

        Applied immediately while no turn runs, otherwise before the next turn
        starts; a running turn keeps its model. The selection survives provider
        reconnects within the cycle. Requires ``MODEL_SELECTION`` and a started
        harness.
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


@runtime_checkable
class ModelConfigProvider(Protocol):
    """Optional provider hook mapping a host's model choice onto its configuration.

    A ``HarnessProvider`` implements it next to ``create`` so a host never
    knows the provider's configuration fields: the host picks a model (and
    effort) from the provider's built-in catalog or from an endpoint it owns,
    and merges the returned fragment into the ``create`` mapping. A choice
    with an ``endpoint`` requires the card to declare ``ENDPOINT_MODELS``.
    """

    def model_config(self, choice: ModelChoice, *, fallback: ModelChoice | None = None) -> JsonObject:
        """Return the configuration fragment that runs ``choice``.

        Args:
            choice: The model to create the harness on.
            fallback: A model the provider may switch to on its own (the
                Claude Code / Codex authentication fallback); providers
                without such a mechanism raise when it is given.

        Raises:
            ValueError: When the provider cannot run the choice, e.g. an
                effort its runtime has no selector for.
        """
        ...


__all__ = ["HarnessModelControl", "HarnessProtocol", "HarnessProvider", "ModelConfigProvider"]
