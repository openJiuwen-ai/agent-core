# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Bind one Registry's control-plane components to one EventBus.

A Core Handler only receives ``(event, ops, event_next)``, so it cannot be
handed the Package Manager, Orchestrator, and Runtime directly. ``Registry``
binds them here at ``init``; a handler then looks the context up from the bus
carried by its request. The mapping holds the bus weakly, so a discarded bus
does not keep an install store alive.
"""

import threading
import weakref
from collections.abc import MutableMapping
from dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.extension.errors import ExtensionFailure
from openjiuwen.core.extension.orchestrator import Orchestrator
from openjiuwen.core.extension.package_manager import PackageManager
from openjiuwen.core.extension.runtime import Runtime


@dataclass(frozen=True, slots=True)
class ManagementContext:
    """The components one Registry uses for every package operation."""

    bus: ExtensionEventBusProtocol
    package_manager: PackageManager | None
    orchestrator: Orchestrator
    runtime: Runtime
    host_version: str | None = None


_LOCK = threading.Lock()
_CONTEXTS: MutableMapping[object, ManagementContext] = weakref.WeakKeyDictionary()


def bind_context(context: ManagementContext) -> None:
    """Attach a context to its bus.

    Raises:
        ExtensionFailure: The bus already has a context. A second Registry
            would otherwise take over the management events of the first.
    """
    with _LOCK:
        if context.bus in _CONTEXTS:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="this event bus already has an initialized registry",
            )
        _CONTEXTS[context.bus] = context


def replace_context(context: ManagementContext) -> None:
    """Replace the context already bound to this bus.

    Raises:
        ExtensionFailure: No Registry has been initialized for the bus.
    """
    with _LOCK:
        if context.bus not in _CONTEXTS:
            raise ExtensionFailure(
                StatusCode.EXTENSION_LOAD_FAILED,
                reason="no registry is initialized for this event bus",
            )
        _CONTEXTS[context.bus] = context


def unbind_context(bus: ExtensionEventBusProtocol) -> None:
    """Detach the context of a bus. Registered event types stay in place."""
    with _LOCK:
        _CONTEXTS.pop(bus, None)


def context_for(bus: ExtensionEventBusProtocol) -> ManagementContext:
    """Return the context bound to a bus.

    Raises:
        ExtensionFailure: No Registry was initialized for this bus.
    """
    with _LOCK:
        context = _CONTEXTS.get(bus)
    if context is None:
        raise ExtensionFailure(
            StatusCode.EXTENSION_LOAD_FAILED,
            reason="no registry is initialized for this event bus",
        )
    return context
