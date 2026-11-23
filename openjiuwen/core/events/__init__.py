# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public events contract: models, protocols and the extension event bus."""

from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.package_lifecycle import (
    HostPackageInstallRequest,
    PackageInstallOutcome,
    register_host_package_lifecycle,
)
from openjiuwen.core.events.protocol import EventHandler, ExtensionEventBusProtocol
from openjiuwen.core.events.types import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventInput,
    EventInputValidator,
    EventOutput,
    EventOutputValidator,
    EventType,
    HandlerId,
    HandlerLevel,
    MatchPattern,
    MatchPatternFunc,
    OpsMethod,
    PackageMetadata,
    SessionContext,
)

__all__ = [
    "EventType",
    "EventInput",
    "EventOutput",
    "HandlerId",
    "OPS_BUILD_EVENT_TYPE",
    "HandlerLevel",
    "Event",
    "SessionContext",
    "PackageMetadata",
    "MatchPattern",
    "MatchPatternFunc",
    "EventInputValidator",
    "EventOutputValidator",
    "EventHandler",
    "EventNext",
    "OpsMethod",
    "EventHandlerOpsBuilder",
    "EventHandlerOps",
    "ExtensionEventBusProtocol",
    "ExtensionEventBus",
    "HostPackageInstallRequest",
    "PackageInstallOutcome",
    "register_host_package_lifecycle",
]
