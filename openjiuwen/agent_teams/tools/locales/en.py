# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""English (en) locale strings for agent team tools.

Key convention
--------------
- ``tool_name._desc``            — ToolCard description
- ``tool_name.param``            — top-level param description
- ``tool_name.nested.param``     — nested schema param (e.g. task item)
"""

STRINGS: dict[str, str] = {
    # ===== build_team ==========================================================
    "build_team._desc": (
        "Create a team with a name and collaboration goal. "
        "This must be called before spawn_member and add_task."
    ),
    "build_team.name": "Team name reflecting the team's functional direction",
    "build_team.desc": "Description of the team collaboration goal and task scope",
    "build_team.prompt": "Team-level global prompt visible to all members",
    "build_team.leader_name": "Display name of the Leader",
    "build_team.leader_desc": "Leader persona (professional background, domain expertise)",

    # ===== clean_team ==========================================================
    "clean_team._desc": (
        "Disband the team and release all resources. "
        "Precondition: every member has been shut down via shutdown_member. "
        "Call after all tasks are completed and results are summarised."
    ),

    # ===== spawn_member ========================================================
    "spawn_member._desc": (
        "Create a new team member by domain expertise. "
        "Each member should have a clear persona and specialisation "
        "for claiming and executing matching tasks. "
        "Newly created members are in an unstarted state; "
        "call startup_members to start them all at once."
    ),
    "spawn_member.member_id": "Unique member identifier; use a semantic ID (e.g. backend-dev-1)",
    "spawn_member.name": "Member name reflecting the role (e.g. 'Backend Expert')",
    "spawn_member.desc": "Member persona including background, expertise, work style — used for task matching",
    "spawn_member.prompt": "Startup instruction; should guide the member to view_task and claim tasks",

    # ===== shutdown_member =====================================================
    "shutdown_member._desc": (
        "Shut down a team member and release resources. "
        "Call after the member finishes all tasks; "
        "use force if the member is stuck."
    ),
    "shutdown_member.member_id": "ID of the member to shut down",
    "shutdown_member.force": "Force shutdown (ignore incomplete tasks), default false",

    # ===== approve_plan ========================================================
    "approve_plan._desc": (
        "Approve or reject a member's execution plan. "
        "Review whether the plan meets the goal and provide feedback."
    ),
    "approve_plan.member_id": "ID of the member who submitted the plan",
    "approve_plan.approved": "true to approve, false to reject and request revision",
    "approve_plan.feedback": "Review feedback; when rejecting, explain the reason and expected changes",

    # ===== approve_tool ========================================================
    "approve_tool._desc": (
        "Approve or reject a teammate tool call interrupted by a rail. "
        "Leader should respond with approved, feedback, and auto_confirm."
    ),
    "approve_tool.member_id": "ID of the member who initiated the tool approval request",
    "approve_tool.tool_call_id": "The interrupted tool_call_id to resume",
    "approve_tool.approved": "Whether to approve this tool call",
    "approve_tool.feedback": "Optional approval feedback",
    "approve_tool.auto_confirm": "Auto-approve future calls to the same tool",

    # ===== list_members ========================================================
    "list_members._desc": "List all team members and their status to assess team composition",

    # ===== task_manager ========================================================
    "task_manager._desc": (
        "Team task management tool (Leader only). "
        "action: add, insert (into existing DAG), update, cancel, cancel_all. "
        "For add, pass tasks as an array (single or batch); "
        "insert supports depended_by for reverse dependencies."
    ),
    "task_manager.action": "Operation type, default add",
    "task_manager.tasks": "Task list for add action (wrap single task in an array too)",
    "task_manager.task_id": "Task ID (custom ID for insert; target ID for update/cancel)",
    "task_manager.title": "Task title (used by insert/update)",
    "task_manager.content": "Task content (used by insert/update)",
    "task_manager.depends_on": "Dependency task IDs for insert",
    "task_manager.depended_by": "Existing task IDs that should wait for this task (reverse dependency for insert)",
    # nested _task_schema
    "task_manager.task.task_id": "Custom task ID for dependency reference",
    "task_manager.task.title": "Task title — concise description of the goal",
    "task_manager.task.content": "Task details including instructions and acceptance criteria",
    "task_manager.task.depends_on": "Prerequisite task IDs",

    # ===== view_task ===========================================================
    "view_task._desc": (
        "View task information. "
        "action: get (single task detail), list (by status), claimable (default, ready to claim)"
    ),
    "view_task.action": "View mode, default claimable",
    "view_task.task_id": "Task ID for get action",
    "view_task.status": "Status filter for list: pending/claimed/plan_approved/completed/cancelled/blocked",

    # ===== claim_task ==========================================================
    "claim_task._desc": (
        "Claim a ready task. Only pending, unclaimed tasks can be claimed; "
        "pick one matching your domain expertise."
    ),
    "claim_task.task_id": "ID of the task to claim; must be in pending status",

    # ===== complete_task =======================================================
    "complete_task._desc": (
        "Mark a task as completed. "
        "Downstream tasks that depend on it will be unblocked to pending. "
        "After calling, report results to the Leader via send_message."
    ),
    "complete_task.task_id": "ID of the task to complete; must be a task you have claimed",

    # ===== send_message ========================================================
    "send_message._desc": (
        "Send a point-to-point message to a member. "
        "Use for task notifications, progress replies, escalations, or dependency coordination."
    ),
    "send_message.content": "Message content with clear action guidance or information",
    "send_message.to_member": "Recipient member ID",

    # ===== broadcast_message ===================================================
    "broadcast_message._desc": (
        "Broadcast a message to all team members. "
        "Use for global decisions, constraint changes, or announcements."
    ),
    "broadcast_message.content": "Broadcast content — all members will receive it",
}
