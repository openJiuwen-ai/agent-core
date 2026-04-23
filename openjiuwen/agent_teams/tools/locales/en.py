# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""English (en) locale strings for agent team tools.

Key convention
--------------
- ``tool_name._desc``            — ToolCard description (lives in ``descs/en/<tool>.md``)
- ``tool_name.param``            — top-level param description
- ``tool_name.nested.param``     — nested schema param (e.g. task item)
"""

STRINGS: dict[str, str] = {
    # ===== build_team ==========================================================
    # build_team._desc lives in descs/en/build_team.md
    "build_team.display_name": "Human-readable display label for the team (e.g. 'Backend Platform Squad')",
    "build_team.team_desc": (
        "Team goal, delivery scope, and global directives. "
        "All members see this — state collaboration goals and constraints clearly"
    ),
    "build_team.leader_display_name": "Human-readable display label for the leader member",
    "build_team.leader_desc": (
        "Leader persona (professional background, domain expertise); "
        "influences how members trust and communicate with you"
    ),
    "build_team.enable_hitt": (
        "Enable Human in the Team (HITT). When true, registers the "
        "reserved human_agent member as a first-class teammate. Set to "
        "true when the user signals intent to join the team "
        "(e.g. 'I want to join', 'Count me in'); default false"
    ),
    # ===== clean_team ==========================================================
    # clean_team._desc lives in descs/en/clean_team.md
    # ===== spawn_member ========================================================
    # spawn_member._desc lives in descs/en/spawn_member.md
    "spawn_member.member_name": (
        "Unique member name (semantic slug, e.g. 'backend-dev-1'); "
        "serves as primary identifier and routing key "
        "for all message/approval/task operations"
    ),
    "spawn_member.display_name": (
        "Human-readable display label for the member (e.g. 'Backend Expert'). "
        "Purely presentational — not used for routing"
    ),
    "spawn_member.desc": (
        "Long-term role profile of the member, including professional background, "
        "core expertise, preferred task types, collaboration style, "
        "and boundaries the member should not own"
    ),
    "spawn_member.prompt": (
        "The first instruction the member receives at startup. "
        "Use it to define initial priorities, task selection guidance, constraints, "
        "or coordination expectations; give clear direction, "
        "avoid generic startup filler, "
        "and do not repeat the generic workflow"
    ),
    # ===== shutdown_member =====================================================
    # shutdown_member._desc lives in descs/en/shutdown_member.md
    "shutdown_member.member_name": (
        "member_name of the member to request shutdown for (semantic slug, not display label)"
    ),
    "shutdown_member.force": (
        "Whether to force shutdown, default false. "
        "Use only when the member is stuck, unresponsive, "
        "or cannot complete a normal shutdown sequence"
    ),
    # ===== approve_plan ========================================================
    # approve_plan._desc lives in descs/en/approve_plan.md
    "approve_plan.member_name": "member_name of the member who submitted the plan (semantic slug, not display label)",
    "approve_plan.approved": (
        "Whether to approve the current plan. true means proceed to implementation; false means revise it"
    ),
    "approve_plan.feedback": (
        "Review feedback. When rejecting, explain the "
        "reason and revision direction; when approving, "
        "you may add constraints, reminders, or extra requirements"
    ),
    # ===== approve_tool ========================================================
    # approve_tool._desc lives in descs/en/approve_tool.md
    "approve_tool.member_name": (
        "member_name of the member who initiated the tool approval request (semantic slug, not display label)"
    ),
    "approve_tool.tool_call_id": (
        "The interrupted tool_call_id to resume; it should match the tool call in the current approval request"
    ),
    "approve_tool.approved": (
        "Whether to approve this tool call. "
        "true means allow it to continue; "
        "false means reject it and require an adjusted approach"
    ),
    "approve_tool.feedback": (
        "Review feedback. When rejecting, explain the "
        "reason and an alternative direction; when approving, "
        "you may add boundaries, risk reminders, or extra constraints"
    ),
    "approve_tool.auto_confirm": (
        "Whether to auto-approve future calls to the same tool. "
        "Default false; enable only when you explicitly "
        "accept continued use of that tool type"
    ),
    # ===== list_members ========================================================
    # list_members._desc lives in descs/en/list_members.md
    # ===== create_task ========================================================
    # create_task._desc lives in descs/en/create_task.md
    "create_task.tasks": "Task list — wrap single tasks in an array too",
    "create_task.task.task_id": "Custom task ID for dependency reference (auto-generated if omitted)",
    "create_task.task.title": "Task title — concise description of the goal",
    "create_task.task.content": "Task details including goals and acceptance criteria",
    "create_task.task.depends_on": "Prerequisite task IDs that must complete first",
    "create_task.task.depended_by": "Existing task IDs that should wait for this task (reverse dependency)",
    # ===== view_task ===========================================================
    # view_task._desc lives in descs/en/view_task.md
    "view_task.action": (
        "View mode: 'list' (default, summary of all tasks), "
        "'get' (single task detail, requires task_id), "
        "'claimable' (pending tasks ready to claim)"
    ),
    "view_task.task_id": "Task ID — required when action=get, ignored otherwise",
    "view_task.status": (
        "Status filter for action=list only: "
        "pending/claimed/plan_approved/completed/cancelled/blocked. "
        "Omit to list all."
    ),
    # ===== update_task =========================================================
    # update_task._desc lives in descs/en/update_task.md
    "update_task.task_id": "Task ID to update, or '*' to cancel all tasks",
    "update_task.status": "Set to 'cancelled' to cancel the task",
    "update_task.title": "New task title",
    "update_task.content": "New task content",
    "update_task.assignee": (
        "member_name to assign this task to (only when currently unassigned). A notification is sent to the assignee"
    ),
    "update_task.add_blocked_by": (
        "Task IDs to add as new dependencies (this task will be blocked until those tasks complete)"
    ),
    "update_task.error_human_agent_locked_cancel": (
        "Task {task_id} is claimed by a human member; this task cannot "
        "be cancelled. Use send_message to coordinate with that human "
        "member instead"
    ),
    "update_task.error_human_agent_locked_reassign": (
        "Task {task_id} is claimed by a human member; it cannot be "
        "reassigned to {new_assignee}. Tasks locked by a human member "
        "must be completed by that human"
    ),
    # ===== claim_task =========================================================
    # claim_task._desc lives in descs/en/claim_task.md
    "claim_task.task_id": "The ID of the task to claim or complete",
    "claim_task.status": "New status: 'claimed' (start work) or 'completed' (mark done)",
    # ===== send_message ========================================================
    # send_message._desc lives in descs/en/send_message.md
    "send_message.to": (
        'Recipient: member_name for point-to-point (e.g. "backend-dev-1"); '
        '"user" (teammates only, to reply to the user); '
        '"*" for broadcast'
    ),
    "send_message.content": "Message content with clear action guidance or information",
    "send_message.summary": "5-10 word summary for message preview and logging",
    # ===== enter_worktree =====================================================
    # enter_worktree._desc lives in descs/en/enter_worktree.md
    "enter_worktree.name": (
        "Optional name for the worktree. "
        'Each "/"-separated segment may contain only letters, '
        "digits, dots, underscores, and dashes; max 64 chars total. "
        "A random name is generated if not provided"
    ),
    # ===== exit_worktree ======================================================
    # exit_worktree._desc lives in descs/en/exit_worktree.md
    "exit_worktree.action": '"keep" leaves the worktree and branch on disk; "remove" deletes both',
    "exit_worktree.discard_changes": (
        'Required true when action is "remove" and '
        "the worktree has uncommitted files or unmerged commits. "
        "The tool will refuse and list them otherwise"
    ),
    # ===== workspace_meta =====================================================
    # workspace_meta._desc lives in descs/en/workspace_meta.md
    "workspace_meta.action": (
        "Operation type: lock (acquire file lock), "
        "unlock (release file lock), "
        "locks (list all active locks), "
        "history (view file version history)"
    ),
    "workspace_meta.path": "Relative path of the target file (required for lock/unlock/history)",
}
