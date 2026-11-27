# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``package.*`` management event types and the shared CORE forwarder.

The events package must not import extension while it loads. The CORE handler
imports the orchestration only when a caller triggers the event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.package_lifecycle import (
    HostPackageInstallRequest,
    PackageInstallOutcome,
    host_install_handler,
    host_outcome_handler,
)
from openjiuwen.core.events.types import Event, EventInput, EventOutput, EventType, MatchPattern

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus
    from openjiuwen.core.events.protocol import EventHandler

PACKAGE_EVENT_TYPES: tuple[EventType, ...] = (
    "package.parse",
    "package.resolve",
    "package.query",
    "package.install",
    "package.install_finish",
    "package.install_failed",
    "package.uninstall",
    "package.reload",
)


def match_pattern_func(event_input: EventInput, match_pattern: MatchPattern) -> bool:
    """Accept the management request object. These events do not use a pattern."""
    del event_input
    return not match_pattern


async def _publish_outcome(
    bus: ExtensionEventBus,
    event_type: EventType,
    outcome: PackageInstallOutcome,
) -> None:
    """Publish one host outcome; a listener failure never changes the load result."""

    try:
        await bus.trigger(Event(event_type, outcome))
    except Exception:  # noqa: BLE001 - the host load result is authoritative
        logger.warning(
            "could not publish %s for %s",
            event_type,
            outcome.package_id,
            exc_info=True,
        )


def _failed_outcome(request: HostPackageInstallRequest, error: str) -> PackageInstallOutcome:
    return PackageInstallOutcome(
        event_id=request.event_id,
        operation=request.operation,
        package_id=request.package_id,
        version=request.version,
        status="failed",
        active=False,
        enabled=False,
        error=error,
        container_id=request.container_id,
        container_version=request.container_version,
    )


def make_core_handler(event_type: EventType, bus: ExtensionEventBus | None = None) -> EventHandler:
    """Forward one package event to the Registry or the registered host callback.

    ``bus`` stays optional so callers written before host lifecycle events keep
    working; without it those events keep the management-dispatch behavior.
    """

    async def core_handler(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> EventOutput | None:
        del ops, event_next
        if bus is not None and event_type == "package.install" and isinstance(
            event.event_input, HostPackageInstallRequest
        ):
            request = event.event_input
            handler = host_install_handler(bus)
            if handler is None:
                return None
            try:
                outcome = await handler(request)
            except Exception as exc:  # noqa: BLE001 - one host failure must not leave the member pending
                outcome = _failed_outcome(request, str(exc))
                await _publish_outcome(bus, "package.install_failed", outcome)
                return outcome
            if not isinstance(outcome, PackageInstallOutcome):
                logger.warning(
                    "host install handler for %s returned %r; publishing a failure",
                    request.package_id,
                    outcome,
                )
                outcome = _failed_outcome(request, "host install handler returned no outcome")
                await _publish_outcome(bus, "package.install_failed", outcome)
                return outcome
            outcome_event = (
                "package.install_finish" if outcome.status == "success" else "package.install_failed"
            )
            await _publish_outcome(bus, outcome_event, outcome)
            return outcome
        if bus is not None and event_type in {"package.install_finish", "package.install_failed"}:
            handler = host_outcome_handler(bus)
            if handler is None:
                return None
            return await handler(event.event_input)
        from openjiuwen.core.extension.management import dispatch_management_event

        return await dispatch_management_event(event_type, event.event_input)

    core_handler.__name__ = f"core_handler_{event_type.replace('.', '_')}"
    return core_handler


def register_management_event(bus: ExtensionEventBus, event_type: EventType) -> None:
    """Register one management event type and its CORE handler."""
    bus.register_event_type(event_type, match_pattern_func, make_core_handler(event_type, bus))


def register(bus: ExtensionEventBus) -> None:
    """Register every ``package.*`` management event."""
    for event_type in PACKAGE_EVENT_TYPES:
        register_management_event(bus, event_type)
