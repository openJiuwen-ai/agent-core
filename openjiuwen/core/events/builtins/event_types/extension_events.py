# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``extension.plan`` / ``extension.apply`` / ``extension.rollback`` event types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.events.builtins.event_types.package_events import register_management_event
from openjiuwen.core.events.types import EventType

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

EXTENSION_EVENT_TYPES: tuple[EventType, ...] = (
    "extension.plan",
    "extension.apply",
    "extension.rollback",
)


def register(bus: ExtensionEventBus) -> None:
    """Register every ``extension.*`` management event."""
    for event_type in EXTENSION_EVENT_TYPES:
        register_management_event(bus, event_type)
