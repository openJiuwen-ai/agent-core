# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""External interaction layer for agent teams.

Home for the two parallel input channels the runtime exposes:

* ``UserInbox`` — the external caller ("user"). Delivers input to the
  leader's DeepAgent by default; ``@member_name`` hops the leader and
  posts directly to the named member.
* ``HumanAgentInbox`` — the reserved ``human_agent`` team member. Only
  available when HITT is enabled; every call is dispatched as a
  ``send_message`` from ``human_agent``.

The mention parser lives here as a pure function so it is independently
testable — it used to sit inline in ``dispatcher.py`` with regex
literals scattered across methods.

For bridge-agent members (a teammate paired with a remote independent
agent reachable over a pure-text protocol) the framework auto-forwards
mailbox messages through ``BridgeProtocolAdapter`` on the recipient's
behalf — there is no user-facing inbox class; the bridge avatar is a
normal teammate from the team's point of view.
"""

from openjiuwen.agent_teams.interaction.bridge_protocol import (
    REMOTE_UNAVAILABLE_SENTINEL,
    BridgeAgentNotEnabledError,
    BridgeProtocolAdapter,
    UnknownBridgeAgentError,
)
from openjiuwen.agent_teams.interaction.human_agent_inbox import (
    HumanAgentInbox,
    HumanAgentNotEnabledError,
    UnknownHumanAgentError,
)
from openjiuwen.agent_teams.interaction.payload import (
    DeliverResult,
    GodViewMessage,
    HumanAgentInboundEvent,
    HumanAgentMessage,
    InteractPayload,
    OperatorMessage,
)
from openjiuwen.agent_teams.interaction.router import (
    is_reserved_name,
    parse_interact_str,
    parse_mention,
)
from openjiuwen.agent_teams.interaction.user_inbox import (
    UserInbox,
)

__all__ = [
    "BridgeAgentNotEnabledError",
    "BridgeProtocolAdapter",
    "DeliverResult",
    "GodViewMessage",
    "HumanAgentInbox",
    "HumanAgentInboundEvent",
    "HumanAgentMessage",
    "HumanAgentNotEnabledError",
    "InteractPayload",
    "OperatorMessage",
    "REMOTE_UNAVAILABLE_SENTINEL",
    "UnknownBridgeAgentError",
    "UnknownHumanAgentError",
    "UserInbox",
    "is_reserved_name",
    "parse_interact_str",
    "parse_mention",
]
