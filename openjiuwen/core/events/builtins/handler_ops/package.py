# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Register methods that read the current handler's package metadata.

``root()`` returns the registered package root; ``name()`` returns its package
id, not its display name. Both return ``None`` without package metadata.
An extension's USER or BUILTIN handler can have metadata; CORE has none.

Extension Runtime supplies metadata when publishing handlers. Direct EventBus
registrations remain responsible for the metadata they provide. These methods
only read those values and do not verify identity, paths, or access rights.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOps,
    EventHandlerOpsBuilder,
    EventNext,
    HandlerLevel,
)

if TYPE_CHECKING:
    from openjiuwen.core.events import ExtensionEventBus


async def package_root(ops: EventHandlerOps) -> Path | None:
    """Return the registered package root, or ``None`` if it is unavailable.

    Extension Runtime supplies an absolute path. This method preserves the
    registered value without resolving it or accessing the filesystem.
    """
    metadata = ops.package_metadata
    if metadata is None:
        return None
    return metadata.root


async def package_name(ops: EventHandlerOps) -> str | None:
    """Return the registered package id, or ``None`` without metadata."""
    metadata = ops.package_metadata
    if metadata is None:
        return None
    return metadata.package_id


async def build_package_ops(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> EventHandlerOpsBuilder:
    """Add the ``package`` namespace to the shared Ops builder."""
    del ops  # Build phase has no per-handler Ops yet.
    builder = await event_next(event)
    if not isinstance(builder, EventHandlerOpsBuilder):
        raise_error(
            StatusCode.EVENTS_OPS_INVALID,
            reason="ops.build did not return EventHandlerOpsBuilder",
        )

    builder.add_namespace("package")
    builder.add_method("package", "root", package_root)
    builder.add_method("package", "name", package_name)
    return builder


def register(bus: ExtensionEventBus) -> None:
    """Register the ``package`` ops.build BUILTIN handler."""
    bus.register_event_handler(
        OPS_BUILD_EVENT_TYPE,
        build_package_ops,
        {},
        HandlerLevel.BUILTIN,
    )
