# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rails specific to TeamAgent.

Layout:
- ``team_policy_rail``: ``TeamPolicyRail`` — injects team-specific
  PromptSections (role, workflow, lifecycle, persona, info, members)
  into the agent's shared system prompt builder.
- ``team_tool_rail``: ``TeamToolRail`` — registers role-appropriate
  team coordination tools onto the agent's ability manager.
- ``first_iteration_gate``: ``FirstIterationGate`` — async signal that
  unblocks once the agent enters its first task-loop iteration.
- ``tool_approval_rail``: ``TeamToolApprovalRail`` — leader-mediated
  approval gate for teammate tool calls.
"""

from __future__ import annotations

from openjiuwen.agent_teams.rails.first_iteration_gate import FirstIterationGate
from openjiuwen.agent_teams.rails.team_policy_rail import TeamPolicyRail
from openjiuwen.agent_teams.rails.team_tool_rail import (
    TeamToolRail,
    qualify_team_tool_ids,
)
from openjiuwen.agent_teams.rails.tool_approval_rail import TeamToolApprovalRail

__all__ = [
    "FirstIterationGate",
    "TeamPolicyRail",
    "TeamToolApprovalRail",
    "TeamToolRail",
    "qualify_team_tool_ids",
]
