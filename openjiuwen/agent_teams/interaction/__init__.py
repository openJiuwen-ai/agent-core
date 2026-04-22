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
"""

from openjiuwen.agent_teams.interaction.human_agent_inbox import (
    HumanAgentInbox,
    HumanAgentNotEnabledError,
)
from openjiuwen.agent_teams.interaction.router import (
    is_reserved_name,
    parse_mention,
)
from openjiuwen.agent_teams.interaction.user_inbox import (
    UserInbox,
)

__all__ = [
    "HumanAgentInbox",
    "HumanAgentNotEnabledError",
    "UserInbox",
    "is_reserved_name",
    "parse_mention",
]
