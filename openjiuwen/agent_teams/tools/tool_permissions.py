# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission sets and member-name validation pattern for team tools."""

import re


# ========== Tool Permission Sets ==========

# Tools that only the leader can use
LEADER_ONLY_TOOLS: set[str] = {
    "build_team",         # Create a new team
    "clean_team",         # Clean up a team
    "spawn_teammate",     # Spawn an ordinary LLM teammate
    "spawn_human_agent",  # Spawn a human member (HITT)
    "spawn_bridge_agent", # Spawn a bridge to a remote agent
    "spawn_external_cli", # Spawn a third-party CLI agent teammate
    "shutdown_member",    # Shutdown a team member
    "approve_plan",       # Approve or reject a member's plan
    "approve_tool",       # Approve or reject a teammate tool call
    "create_task",        # Create tasks (batch / with deps)
    "update_task",        # Update task content / cancel tasks
    "swarmflow",          # Run a swarmflow orchestration script (gated by enable_swarmflow)
    # Async-tool control — inspect / fetch / cancel background async tasks
    "async_tasks_list",
    "async_task_output",
    "async_task_cancel",
}

# Tools that only members can use
MEMBER_ONLY_TOOLS: set[str] = {
    "claim_task",  # Claim or complete a task
    "submit_plan", # Submit a plan before executing in plan_mode
    # Worktree tools — members work in isolated worktrees
    # "enter_worktree",          # Enter an isolated git worktree
    # "exit_worktree",           # Exit the current worktree session
}

# Tools that both leader and members can use
SHARED_TOOLS: set[str] = {
    # Query tools
    # "get_team_info",           # Get team information
    # "get_member",              # Get member information
    "view_task",      # View tasks (unified - supports get/list/claimable)
    # Messaging tools
    "send_message",   # Send a message (point-to-point or broadcast)
    "workspace_meta", # Workspace lock management and version history
}

# All tools available to leader
LEADER_TOOLS: set[str] = LEADER_ONLY_TOOLS | SHARED_TOOLS

# All tools available to members
MEMBER_TOOLS: set[str] = MEMBER_ONLY_TOOLS | SHARED_TOOLS

# Tools available to the reserved ``human_agent`` role. The human
# agent acts on its corresponding external user's behalf, so it gets
# read access to tasks, a self-only completion tool, and the shared
# ``send_message`` tool so the user can ask the avatar to relay a
# message to other members ("tell the leader I'm in a meeting").
# The behavioural constraint — that the avatar must **not** speak on
# its own initiative and may only send when the user explicitly
# instructs a relay — is enforced in the HITT system prompt section,
# not in the tool's ``invoke``. The user's own voice still flows
# through ``HumanAgentInbox`` with explicit ``@target`` routing; this
# tool is a complementary path for user-driven outbound speech.
#
# ``claim_task`` is intentionally absent — autonomous claiming is a
# teammate behavior; the user's avatar must wait for explicit leader
# assignment via ``update_task(assignee=...)`` instead.
#
# Workspace lock/version (``workspace_meta``) is attached separately
# by ``TeamToolRail`` whenever a workspace_manager is configured —
# same path leader/teammate use — so this set doesn't list it.
HUMAN_AGENT_TOOLS: set[str] = {
    "view_task",
    "member_complete_task",
    "send_message",
}


# ``member_name`` is used verbatim as a primary key, a message routing
# token, and a path segment under ``team_home``. Allowing non-ASCII
# (e.g. CJK) or shell-significant characters in any of those positions
# silently breaks routing on some transports and produces unreadable
# directory layouts on disk. Restrict to the DNS-label-style alphabet
# used everywhere else in the ecosystem (k8s pods, docker containers):
# lowercase ASCII letters, digits, and hyphen, with a leading letter so
# the name is never a bare number or starts with a separator. Enforced
# at the only LLM-facing entry point (``spawn_member``).
_MEMBER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
