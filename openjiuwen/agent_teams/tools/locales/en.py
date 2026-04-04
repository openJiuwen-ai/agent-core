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
    "build_team._desc": """\
Assemble a team and register yourself as Leader. Call as soon as you have a goal — don't hesitate.

## Call Order
build_team → task_manager(add) → spawn_member → send_message(to="*").
No other team tool may be called before build_team.

## Task Design Principles
- Describe goals, not steps: content should contain goals, acceptance criteria, \
and constraints — not specific operations
- Single owner: each task may only be claimed by one teammate who owns delivery
- Coarse-grained: one task = one independently deliverable outcome
- Member autonomy: members create their own plans; Leader reviews via approve_plan

## Team Workflow
1. Analyze the problem, clarify goals. Ask the user if requirements are ambiguous
2. Call build_team (system auto-registers you as a member)
3. Use task_manager(add) to create the task DAG. \
**All tasks must be created before creating members**
4. Task self-review: verify dependencies, chain reasonableness, coverage completeness
5. Use spawn_member to create domain specialists
6. Use send_message(to="*") to send the startup signal — \
system auto-launches all unstarted members
7. Members autonomously claim tasks, create plans, execute deliveries
8. Respond to notifications: review plans, answer questions, arbitrate conflicts
9. Dynamically add members with spawn_member, then send_message(to="*") to launch
10. When done: shutdown_member to close members; clean_team to dissolve temporary teams

## Automatic Message Delivery
After sending messages, don't poll for replies or check task progress. \
The system proactively notifies you when new messages arrive or task states change. \
If nothing is pending, stop and wait for notifications.

## Member Idle State
Members won't reply immediately after startup — they need time to review tasks, \
create plans, and execute work. Idle is normal, not an error. \
Don't nudge, re-send, or shut down members. \
Only intervene when a member has made no progress for an extended period \
and hasn't reported any blocker.""",
    "build_team.team_name": "Team name reflecting the team's functional direction — avoid generic names",
    "build_team.team_desc": """\
Team goal, delivery scope, and global directives. \
All members see this — state collaboration goals and constraints clearly""",
    "build_team.leader_name": "Display name of the Leader",
    "build_team.leader_desc": """\
Leader persona (professional background, domain expertise); \
influences how members trust and communicate with you""",

    # ===== clean_team ==========================================================
    "clean_team._desc": """\
Disband the team and delete all resources (team record, members, tasks — cascade delete).

**IMPORTANT**: clean_team will fail if any member is not in SHUTDOWN status. \
Use shutdown_member to close every member first, then call clean_team.

Call after all tasks are completed and results are summarised. \
Returns: {success, data: {team_id}} on success.""",

    # ===== spawn_member ========================================================
    "spawn_member._desc": """\
Create a new team member by domain expertise. \
Each member should have a clear persona and specialisation \
for claiming and executing matching tasks.

Newly created members are unstarted; \
the system auto-launches all unstarted members on the first send_message call.

desc tips: state professional background and domain expertise clearly; \
members use this to decide which tasks to claim. Specialists outperform generalists.""",
    "spawn_member.member_id": "Unique member identifier; use a semantic ID (e.g. backend-dev-1)",
    "spawn_member.name": "Member name reflecting the role (e.g. 'Backend Expert')",
    "spawn_member.desc": "Member persona including background, expertise, work style — used for task matching",
    "spawn_member.prompt": "Startup instruction; should guide the member to view_task and claim tasks",

    # ===== shutdown_member =====================================================
    "shutdown_member._desc": """\
Shut down a team member and release resources. \
Call after the member finishes all tasks; use force if the member is stuck.""",
    "shutdown_member.member_id": "ID of the member to shut down",
    "shutdown_member.force": "Force shutdown (ignore incomplete tasks), default false",

    # ===== approve_plan ========================================================
    "approve_plan._desc": """\
Approve or reject a member's execution plan. \
Review whether the plan meets the goal and provide feedback.""",
    "approve_plan.member_id": "ID of the member who submitted the plan",
    "approve_plan.approved": "true to approve, false to reject and request revision",
    "approve_plan.feedback": "Review feedback; when rejecting, explain the reason and expected changes",

    # ===== approve_tool ========================================================
    "approve_tool._desc": """\
Approve or reject a teammate tool call interrupted by a rail. \
Leader should respond with approved, feedback, and auto_confirm.""",
    "approve_tool.member_id": "ID of the member who initiated the tool approval request",
    "approve_tool.tool_call_id": "The interrupted tool_call_id to resume",
    "approve_tool.approved": "Whether to approve this tool call",
    "approve_tool.feedback": "Optional approval feedback",
    "approve_tool.auto_confirm": "Auto-approve future calls to the same tool",

    # ===== list_members ========================================================
    "list_members._desc": """\
List all team members and their status. \
**When**: First call after startup to understand team composition and each member's expertise""",

    # ===== task_manager ========================================================
    "task_manager._desc": """\
Team task management tool (Leader only). \
**Tasks should focus on deliverable outcomes and acceptance criteria, not execution steps.**

- **add** (default): Pass via `tasks` array (wrap single tasks in an array too). \
Each task contains title, content (goals and acceptance criteria), optional task_id, depends_on. \
**When**: Batch-create the task DAG at project start, or add new tasks during execution
- **insert**: Insert a task into the middle of an existing DAG. \
Pass title, content, optional task_id, depends_on, depended_by (reverse dependency). \
**When**: A missing prerequisite needs to be inserted into an existing dependency chain
- **update**: Pass task_id, title/content. \
If the task is already claimed, the system automatically cancels the member and resets task state. \
**When**: Adjust task scope or add details based on member feedback
- **cancel**: Pass task_id. \
If the task is already claimed, the system automatically cancels the member. \
**When**: Requirement changes make a task unnecessary
- **cancel_all**: Cancel all tasks and all executing members. \
**When**: Fundamental goal changes require complete re-planning""",
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
    "view_task._desc": """\
View task information.

- **get**: Pass task_id for single task detail. \
**When**: Check task state after a member reports progress, or review acceptance criteria
- **list**: Pass status (optional) to filter (pending/claimed/completed/cancelled/blocked). \
**When**: Review overall progress, identify bottleneck tasks, find dependency task executors
- **claimable** (default): Get all pending tasks ready to claim. \
**When**: Find ready tasks to notify members, or after completing a task to find the next one""",
    "view_task.action": "View mode, default claimable",
    "view_task.task_id": "Task ID for get action",
    "view_task.status": "Status filter for list: pending/claimed/plan_approved/completed/cancelled/blocked",

    # ===== claim_task ==========================================================
    "claim_task._desc": """\
Claim a ready task. Only pending, unclaimed tasks can be claimed; \
pick one matching your domain expertise.""",
    "claim_task.task_id": "ID of the task to claim; must be in pending status",

    # ===== complete_task =======================================================
    "complete_task._desc": """\
Mark a task as completed. \
Downstream tasks that depend on it will be unblocked to pending. \
After calling, report results to the Leader via send_message.""",
    "complete_task.task_id": "ID of the task to complete; must be a task you have claimed",

    # ===== send_message ========================================================
    "send_message._desc": """\
Send a message to team members. \
Use for task assignment notifications, progress reports, \
escalation of blockers, or dependency coordination.

| `to` | |
|---|---|
| member name | Point-to-point message |
| `"*"` | Broadcast — expensive (linear in team size), \
use only for global decisions, constraint changes, or announcements everyone needs |

Your plain text output is NOT visible to other agents — to communicate, you MUST call this tool. \
Messages from teammates are delivered automatically; you don't poll. \
Refer to members by name, never by internal ID. \
When relaying, don't quote the original — it's already rendered to the user.""",
    "send_message.to": 'Recipient: member name for point-to-point, "*" for broadcast',
    "send_message.content": "Message content with clear action guidance or information",
    "send_message.summary": "5-10 word summary for message preview and logging",
}
