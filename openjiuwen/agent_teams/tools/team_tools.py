# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Backward-compat re-export hub for team tools.

All implementations live in the domain-specific modules:
  tool_base.py        — MappedToolOutput, TeamTool
  tool_permissions.py — permission sets, _MEMBER_NAME_PATTERN
  tool_team.py        — BuildTeamTool, CleanTeamTool
  tool_member.py      — spawn/shutdown/approve/list tools
  tool_task.py        — task create/view/update/submit/claim tools
  tool_message.py     — SendMessageTool
  tool_factory.py     — create_team_tools, _wrap_invoke_with_logging

New code should import directly from those modules.
"""

from openjiuwen.agent_teams.tools.tool_base import MappedToolOutput, TeamTool
from openjiuwen.agent_teams.tools.tool_factory import create_team_tools
from openjiuwen.agent_teams.tools.tool_member import (
    ApprovePlanTool,
    ApproveToolCallTool,
    ListMembersTool,
    ShutdownMemberTool,
    SpawnBridgeAgentTool,
    SpawnExternalCliTool,
    SpawnHumanAgentTool,
    SpawnTeammateTool,
)
from openjiuwen.agent_teams.tools.tool_message import SendMessageTool
from openjiuwen.agent_teams.tools.tool_permissions import (
    HUMAN_AGENT_TOOLS,
    LEADER_ONLY_TOOLS,
    LEADER_TOOLS,
    MEMBER_ONLY_TOOLS,
    MEMBER_TOOLS,
    SHARED_TOOLS,
)
from openjiuwen.agent_teams.tools.tool_task import (
    ClaimTaskTool,
    MemberCompleteTaskTool,
    SubmitPlanTool,
    TaskCreateTool,
    UpdateTaskTool,
    ViewTaskToolV2,
)
from openjiuwen.agent_teams.tools.tool_team import BuildTeamTool, CleanTeamTool

__all__ = [
    "MappedToolOutput",
    "TeamTool",
    "HUMAN_AGENT_TOOLS",
    "LEADER_ONLY_TOOLS",
    "LEADER_TOOLS",
    "MEMBER_ONLY_TOOLS",
    "MEMBER_TOOLS",
    "SHARED_TOOLS",
    "BuildTeamTool",
    "CleanTeamTool",
    "ApprovePlanTool",
    "ApproveToolCallTool",
    "ListMembersTool",
    "ShutdownMemberTool",
    "SpawnBridgeAgentTool",
    "SpawnExternalCliTool",
    "SpawnHumanAgentTool",
    "SpawnTeammateTool",
    "ClaimTaskTool",
    "MemberCompleteTaskTool",
    "SubmitPlanTool",
    "TaskCreateTool",
    "UpdateTaskTool",
    "ViewTaskToolV2",
    "SendMessageTool",
    "create_team_tools",
]
