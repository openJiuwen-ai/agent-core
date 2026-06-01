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
        "Per-instance HITT (Human in the Team) switch; accepts true / false / omitted. "
        "Omitted: inherit TeamAgentSpec.enable_hitt (the spec-level capability ceiling). "
        "true: explicitly enable for this build — requires spec.enable_hitt=True or it errors. "
        "false: explicitly disable — every spawn_human_agent request is rejected and any "
        "predefined HUMAN_AGENT members in the spec are skipped. "
        "Use true when the user wants to join the team; false when human collaboration is "
        "explicitly out of scope"
    ),
    # ===== clean_team ==========================================================
    # clean_team._desc lives in descs/en/clean_team.md
    # ===== spawn_member ========================================================
    # spawn_member._desc lives in descs/en/spawn_member.md
    "spawn_member.member_name": (
        "[PUBLIC] Unique member name (semantic slug, e.g. 'backend-dev-1', "
        "DNS-label-style kebab-case). **Must start with a lowercase ASCII "
        "letter (a-z); the rest may be lowercase letters, digits (0-9) or "
        "hyphen (-)** — no uppercase, underscore, whitespace, CJK or any "
        "other non-ASCII characters. Serves as the primary identifier and "
        "the routing key for all message/approval/task operations; must be "
        "unique within the team"
    ),
    "spawn_member.display_name": (
        "[PUBLIC] Human-readable display label for the member (e.g. 'Backend Expert'); "
        "purely presentational, not used for routing. "
        "Injected into every other member's system prompt and returned by list_members — "
        "do not put private content here"
    ),
    "spawn_member.desc": (
        "[PUBLIC] Long-term role profile of the member, including professional background, "
        "core expertise, preferred task types, collaboration style, "
        "and boundaries the member should not own. "
        "Injected into every other member's system prompt and returned by list_members — "
        "never put your internal assessment of the member, sensitive goals, "
        "or confidential strategy here"
    ),
    "spawn_member.role_type": (
        "[INTERNAL] Optional. Member role type — drives framework wiring, "
        "never rendered into any member's prompt text. "
        "'teammate' (default) = regular LLM teammate, "
        "requires model_name/prompt to drive its avatar. 'human_agent' = human "
        "member driven by the real user via HumanAgentInbox; **rejects** model_name "
        "and prompt (the framework template manages them) — passing those fields "
        "raises an error. Choosing 'human_agent' requires spec.enable_hitt=True and "
        "the current build_team instance must not have disabled HITT. "
        "'bridge_agent' = bridge to an external independent agent (e.g. claudecode "
        "/ codex / hermes). Behaves as a full teammate locally (claims tasks, "
        "sends/receives messages); concrete work output is produced by the remote "
        "agent reached over a pure-text protocol, and the local LLM only "
        "schedules — it passes the remote's output through verbatim. Choosing "
        "'bridge_agent' requires non-empty 'desc' (used both as the teammate "
        "persona and the remote's connect briefing) and optional "
        "mailbox_inject_mode / protocol / adapter_config / model_name. Requires "
        "spec.enable_bridge=True and the current build_team instance must not have "
        "disabled Bridge. "
        "'external_cli' = launch a third-party CLI agent (claudecode / codex / ...) "
        "directly as a teammate; its brain is the CLI subprocess rather than a local "
        "LLM, and it sends messages / claims tasks through the auto-injected team MCP "
        "tools. Choosing 'external_cli' requires 'cli_agent' (the CLI kind) and "
        "non-empty 'desc' (the member persona), and rejects model_name/prompt (the "
        "model and config live on the CLI side). 'cli_agent' must name a CLI kind "
        "pre-declared in spec.external_cli_agents"
    ),
    "spawn_member.cli_agent": (
        "Only used when role_type='external_cli'. Identifier of the third-party CLI "
        "agent kind to launch, e.g. 'claude' (claudecode) or 'codex'. Must match a "
        "static config entry pre-declared in spec.external_cli_agents — the launch "
        "command, working directory and MCP injection all live in that entry; this "
        "field only references it by name"
    ),
    "spawn_member.prompt": (
        "[PRIVATE, visible only to this member] Long-term working conventions, "
        "injected only into this member's own system prompt: stable working style, "
        "technical preferences, collaboration constraints, "
        "and any hidden goals or sensitive directives meant only for this member. "
        "Do not write current-batch tasks or generic startup filler such as "
        "'start working' or 'check the task list'. "
        "Forbidden when role_type='human_agent'"
    ),
    "spawn_member.model_name": (
        "Optional. Suggested model name for this member "
        "(e.g. gpt-4, claude-sonnet-4); "
        "the system picks an appropriate model when omitted. "
        "Forbidden when role_type='human_agent'; "
        "for role_type='bridge_agent' it selects the local scheduler LLM"
    ),
    "spawn_member.mailbox_inject_mode": (
        "Only used when role_type='bridge_agent'. Controls how team-side "
        "mailbox messages are wrapped before being relayed to the remote agent. "
        "'passthrough' (default) prefixes only the sender label; 'rephrase' "
        "wraps full sender context (role, persona, optional task hint)"
    ),
    "spawn_member.protocol": (
        "Only used when role_type='bridge_agent'. Protocol identifier "
        "(e.g. 'a2a' / 'acp' / 'claudecode'). Reserved for future "
        "BridgeProtocolAdapter lookup; empty string means no adapter is "
        "wired yet (bridge degrades to a normal teammate)"
    ),
    "spawn_member.adapter_config": (
        "Only used when role_type='bridge_agent'. Free-form adapter "
        "configuration (endpoint, auth, relay_timeout_s, ...). Passed "
        "verbatim to BridgeProtocolAdapter.connect — schema is up to the "
        "concrete adapter implementation"
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
    "approve_plan.plan_id": "Member plan submission ID to approve or reject one exact plan revision.",
    "approve_plan.approved": (
        "Whether to approve the current plan. true means proceed to implementation; false means revise it"
    ),
    "approve_plan.feedback": (
        "Review feedback. When rejecting, explain the "
        "reason and revision direction; when approving, "
        "you may add constraints, reminders, or extra requirements"
    ),
    # ===== submit_plan ==========================================================
    "submit_plan._desc": "Submit a prepared execution-plan Markdown file for a plan-mode task before implementation",
    "submit_plan.task_id": "Task ID to plan before execution",
    "submit_plan.plan_id": "Optional member plan ID; the system generates one when omitted. "
                           "The Leader uses this plan_id for review",
    "submit_plan.plan_path": "Path to the member-authored Markdown plan file; the system copies "
                             "it to a managed snapshot for Leader review",
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
    # ===== member_complete_task ===============================================
    # member_complete_task._desc lives in descs/en/member_complete_task.md
    "member_complete_task.task_id": (
        "ID of the task to mark completed (must be a task the leader has assigned to you)"
    ),
    "member_complete_task.note": (
        "Optional completion note describing your result or any follow-up the team should know about"
    ),
    # ===== send_message ========================================================
    # send_message._desc lives in descs/en/send_message.md
    "send_message.to": (
        'Recipient: member_name for a point-to-point DM (e.g. "backend-dev-1"), '
        "visible only to you and that member; "
        'array of member names (e.g. ["m1","m2"]) for multicast — same content sent '
        "as separate messages to each member, cost is linear in recipient count and "
        "MORE expensive than broadcast for the same audience, use only when truly needed "
        'and cannot mix with "*"/"user"; '
        '"user" (teammates only, to reply to the user); '
        '"*" to broadcast on the team channel, visible to all members'
    ),
    "send_message.content": "Message content with clear action guidance or information",
    "send_message.summary": "5-10 word summary for message preview and logging",
    # NOTE: worktree tools (enter_worktree / exit_worktree) live in
    # ``openjiuwen.harness.tools.worktree`` and resolve their description
    # / param schema via ``harness.prompts.tools`` providers — no entries
    # in this dict.
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
