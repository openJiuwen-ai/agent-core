# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Make sure one bus knows the nine package management event types."""

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.builtins.event_types.extension_events import EXTENSION_EVENT_TYPES
from openjiuwen.core.events.builtins.event_types.package_events import (
    PACKAGE_EVENT_TYPES,
    register_management_event,
)
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol


def ensure_management_events(bus: ExtensionEventBusProtocol) -> None:
    """Register every management event type the bus does not have yet.

    ``ExtensionEventBus`` registers builtin event types while it is constructed,
    so finding them already in place is the normal case. The bus exposes no
    lookup; a duplicate is recognised from the registration error it raises.
    Any other registration failure is passed on.
    """
    for event_type in (*PACKAGE_EVENT_TYPES, *EXTENSION_EVENT_TYPES):
        try:
            register_management_event(bus, event_type)
        except ValidationError as error:
            if error.status is not StatusCode.EVENTS_REGISTRATION_INVALID:
                raise
