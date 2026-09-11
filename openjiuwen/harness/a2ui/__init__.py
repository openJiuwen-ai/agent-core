# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""A2UI ReAct agent + WebSocket gateway extension.

A single ``ReActAgent`` built on openJiuwen, exposed over one WebSocket
endpoint that streams A2UI (GenUI) JSON to any client speaking the
envelope protocol implemented by the Flutter ``a2ui_mobile_app`` client.

Public API re-exports for simplified imports:
    from openjiuwen.harness.a2ui import build_agent, create_app, ConnectionSession
"""

from openjiuwen.harness.a2ui.core.agent import build_agent
from openjiuwen.harness.a2ui.core.rails import A2uiToolEventRail
from openjiuwen.harness.a2ui.server.models import Envelope, make_envelope
from openjiuwen.harness.a2ui.server.server import create_app
from openjiuwen.harness.a2ui.server.ws_session import ConnectionSession

__all__ = [
    "build_agent",
    "Envelope",
    "make_envelope",
    "A2uiToolEventRail",
    "create_app",
    "ConnectionSession",
]
