# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TeamAgent coordination subsystem.

Public surface:
    - CoordinationKernel: lifecycle + transport wiring
    - EventBus: queue + polling timer
    - EventDispatcher: trigger rules + AsyncCallbackFramework registration
    - DispatcherHost: protocol callbacks the dispatcher needs from the host
    - Scenario handlers under :mod:`coordination.handlers`:
      AgentLifecycleHandler / MemberHandler / MessageHandler /
      TaskBoardHandler / StaleTaskHandler (+ BaseCoordinationHandler)
    - InnerEventMessage / InnerEventType / CoordinationEvent / WakeCallback
"""

from __future__ import annotations

from openjiuwen.agent_teams.agent.coordination.dispatcher import (
    DispatcherHost,
    EventDispatcher,
)
from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    EventBus,
    InnerEventMessage,
    InnerEventType,
    WakeCallback,
)
from openjiuwen.agent_teams.agent.coordination.handlers import (
    AgentLifecycleHandler,
    BaseCoordinationHandler,
    MemberHandler,
    MessageHandler,
    StaleTaskHandler,
    TaskBoardHandler,
)
from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel

__all__ = [
    "AgentLifecycleHandler",
    "BaseCoordinationHandler",
    "CoordinationEvent",
    "CoordinationKernel",
    "DispatcherHost",
    "EventBus",
    "EventDispatcher",
    "InnerEventMessage",
    "InnerEventType",
    "MemberHandler",
    "MessageHandler",
    "StaleTaskHandler",
    "TaskBoardHandler",
    "WakeCallback",
]
