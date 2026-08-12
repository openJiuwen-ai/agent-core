# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""English (en) locale strings for agent team tools.

Key convention
--------------
- ``tool_name._desc``            — ToolCard description (lives in ``descs/en/<domain>/<tool>.md``)
- ``tool_name.param``            — top-level param description
- ``tool_name.nested.param``     — nested schema param (e.g. task item)
"""

STRINGS: dict[str, str] = {
    # ===== build_team ==========================================================
    # build_team._desc lives in descs/en/team/build_team.md
    "build_team.display_name": "Human-readable display label for the team (e.g. 'Backend Platform Squad')",
    "build_team.team_desc": (
        "Team goal, delivery scope, and global directives. "
        "All members see this — state collaboration goals and constraints clearly"
    ),
    "build_team.leader_display_name": "Human-readable display label for the leader member",
    "build_team.leader_desc": (
        "Leader description (professional background, domain expertise); "
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
    "build_team.enable_task_verification": (
        "Whether the verify gate runs for this instance; accepts true / false / "
        "omitted (inherit the TeamAgentSpec setting). When on, a teammate completing a task "
        "that carries reviewers moves it to IN_REVIEW for a verdict; when off it completes "
        "directly and any reviewer written into create_task / update_task is ignored. "
        "The parameter is inert when the user's config is false — the value that actually "
        "took effect comes back as task_verification in the result"
    ),
    # ===== checkpoint ==========================================================
    # checkpoint._desc lives in descs/en/member/checkpoint.md
    "checkpoint.name": "Semantic snapshot name (e.g. code-ready). Used by later fork calls to reference this snapshot",
    "checkpoint.description": "Optional description of why this checkpoint was taken",
    # ===== clean_team ==========================================================
    # clean_team._desc lives in descs/en/team/clean_team.md
    # ===== spawn_teammate ======================================================
    # spawn_teammate._desc lives in descs/en/member/spawn_teammate.md
    "spawn_teammate.member_name": (
        "[PUBLIC] Unique member name (semantic slug, e.g. 'backend-dev-1', "
        "DNS-label-style kebab-case). **Must start with a lowercase ASCII "
        "letter (a-z); the rest may be lowercase letters, digits (0-9) or "
        "hyphen (-)** — no uppercase, underscore, whitespace, CJK or any "
        "other non-ASCII characters. Serves as the primary identifier and "
        "the routing key for all message/approval/task operations; must be "
        "unique within the team"
    ),
    "spawn_teammate.display_name": (
        "[PUBLIC] Human-readable display label for the member (e.g. 'Backend Expert'); "
        "purely presentational, not used for routing. "
        "Injected into every other member's system prompt and returned by list_members — "
        "do not put private content here"
    ),
    "spawn_teammate.desc": (
        "[PUBLIC] Long-term role profile of the member, including professional background, "
        "core expertise, preferred task types, collaboration style, "
        "and boundaries the member should not own. "
        "Injected into every other member's system prompt and returned by list_members — "
        "never put your internal assessment of the member, sensitive goals, "
        "or confidential strategy here"
    ),
    "spawn_teammate.prompt": (
        "[PRIVATE, visible only to this member] Long-term working conventions, "
        "injected only into this member's own system prompt: stable working style, "
        "technical preferences, collaboration constraints, "
        "and any hidden goals or sensitive directives meant only for this member. "
        "Do not write current-batch tasks or generic startup filler such as "
        "'start working' or 'check the task list'"
    ),
    "spawn_teammate.model_name": (
        "Optional. Suggested model name for this member "
        "(e.g. gpt-4, claude-sonnet-4); "
        "the system picks an appropriate model when omitted"
    ),
    "spawn_teammate.permissions": (
        "Narrow the teammate's tool permissions (only tightening, never loosening). "
        "Keys are tool names, values are permission levels: 'allow', 'ask', or 'deny'. "
        "Example: {\"bash\": \"deny\", \"write_file\": \"ask\"}"
    ),
    "spawn_teammate.fork": (
        "Inherit context from an existing member to skip repeated file reading "
        "and searching. true: inherit the caller's full current context. "
        "A string (e.g. 'code-ready'): inherit from the named checkpoint snapshot. "
        "Omit for a fresh empty context. All inherited SystemMessages are stripped "
        "during capture — the source member's role identity never leaks to the new member"
    ),
    "spawn_teammate.fork_source": (
        "Name of the member whose context to fork from. Defaults to the leader. "
        "Set to a teammate name (e.g. 'understander') to fork from that member. "
        "The source member must already be spawned and running in-process"
    ),
    "spawn_teammate.compact": (
        "Compact the inherited context before injection. Messages before "
        "the checkpoint are compressed into a summary; messages after "
        "are kept verbatim. Only effective with a named checkpoint fork"
    ),
    # ===== spawn_human_agent ===================================================
    # spawn_human_agent._desc lives in descs/en/member/spawn_human_agent.md
    "spawn_human_agent.member_name": (
        "[PUBLIC] Unique name for the human member (semantic slug, e.g. "
        "'product-owner', DNS-label-style kebab-case). **Must start with a "
        "lowercase ASCII letter (a-z); the rest may be lowercase letters, "
        "digits (0-9) or hyphen (-)** — no uppercase, underscore, whitespace, "
        "CJK or any other non-ASCII characters. Serves as the primary "
        "identifier and routing key; must be unique within the team"
    ),
    "spawn_human_agent.display_name": (
        "[PUBLIC] Human-readable display label for the human member "
        "(e.g. 'Product Owner'); purely presentational, not used for routing. "
        "Injected into every other member's system prompt and returned by "
        "list_members — do not put private content here"
    ),
    "spawn_human_agent.desc": (
        "[PUBLIC] Role profile and responsibilities of the human member, used "
        "for display and description persistence and injected into other members' "
        "system prompts / returned by list_members. The real user drives this "
        "member via HumanAgentInbox; the model and startup prompt are managed "
        "by the framework template, so do not provide them here"
    ),
    # ===== spawn_bridge_agent ==================================================
    # spawn_bridge_agent._desc lives in descs/en/member/spawn_bridge_agent.md
    "spawn_bridge_agent.member_name": (
        "[PUBLIC] Unique name for the bridge member (semantic slug, e.g. "
        "'remote-claude-1', DNS-label-style kebab-case). **Must start with a "
        "lowercase ASCII letter (a-z); the rest may be lowercase letters, "
        "digits (0-9) or hyphen (-)** — no uppercase, underscore, whitespace, "
        "CJK or any other non-ASCII characters. Serves as the primary "
        "identifier and routing key; must be unique within the team"
    ),
    "spawn_bridge_agent.display_name": (
        "[PUBLIC] Human-readable display label for the bridge member "
        "(e.g. 'Remote Claude'); purely presentational, not used for routing. "
        "Injected into every other member's system prompt and returned by "
        "list_members — do not put private content here"
    ),
    "spawn_bridge_agent.desc": (
        "[PUBLIC] Public roster description of the bridge member — how peers "
        "recognise it in list_members / the team roster. Optional. Injected "
        "into other members' system prompts and returned by list_members — do "
        "not put private content here"
    ),
    "spawn_bridge_agent.prompt": (
        "[PRIVATE] The system prompt the remote agent adopts to act as this "
        "member (this member's own private working setup). **Required**: sent "
        "to the remote via adapter.connect. Visible only to this member, never "
        "shown in peers' roster"
    ),
    "spawn_bridge_agent.mailbox_inject_mode": (
        "Controls how team-side mailbox messages are wrapped before being "
        "relayed to the remote agent. 'passthrough' (default) prefixes only "
        "the sender label; 'rephrase' wraps full sender context (role, "
        "desc, optional task hint)"
    ),
    "spawn_bridge_agent.protocol": (
        "Protocol identifier (e.g. 'a2a' / 'acp' / 'claudecode'). Reserved for "
        "future BridgeProtocolAdapter lookup; empty string means no adapter is "
        "wired yet (bridge degrades to a normal teammate)"
    ),
    "spawn_bridge_agent.adapter_config": (
        "Free-form adapter configuration (endpoint, auth, relay_timeout_s, "
        "...). Passed verbatim to BridgeProtocolAdapter.connect — schema is up "
        "to the concrete adapter implementation"
    ),
    "spawn_bridge_agent.model_name": (
        "Optional. Model name for the local scheduler LLM "
        "(e.g. gpt-4, claude-sonnet-4); the system picks one when omitted. "
        "Note the remote agent's model lives on its own side, not controlled "
        "by this field"
    ),
    # ===== spawn_external_cli ===================================================
    # spawn_external_cli._desc lives in descs/en/member/spawn_external_cli.md
    "spawn_external_cli.member_name": (
        "[PUBLIC] Unique name for the CLI member (semantic slug, e.g. "
        "'cli-coder-1', DNS-label-style kebab-case). **Must start with a "
        "lowercase ASCII letter (a-z); the rest may be lowercase letters, "
        "digits (0-9) or hyphen (-)** — no uppercase, underscore, whitespace, "
        "CJK or any other non-ASCII characters. Serves as the primary "
        "identifier and routing key; must be unique within the team"
    ),
    "spawn_external_cli.display_name": (
        "[PUBLIC] Human-readable display label for the CLI member "
        "(e.g. 'Claude CLI Coder'); purely presentational, not used for "
        "routing. Injected into every other member's system prompt and "
        "returned by list_members — do not put private content here"
    ),
    "spawn_external_cli.desc": (
        "[PUBLIC] Public roster description of this CLI member — how peers "
        "recognise it in list_members / the team roster. Optional. Injected "
        "into other members' system prompts and returned by "
        "list_members — do not put private content here"
    ),
    "spawn_external_cli.prompt": (
        "[PRIVATE] The private system prompt this CLI member adopts to act as "
        "this member. **Required**. Visible only to this member, never shown "
        "in peers' roster"
    ),
    "spawn_external_cli.cli_agent": (
        "Identifier of the third-party CLI agent kind to launch, e.g. 'claude' "
        "(claudecode) or 'codex'. Must match a static config entry pre-declared "
        "in spec.external_cli_agents — the launch command, working directory "
        "and MCP injection all live in that entry; this field only references "
        "it by name"
    ),
    # ===== shutdown_member =====================================================
    # shutdown_member._desc lives in descs/en/member/shutdown_member.md
    "shutdown_member.member_name": (
        "member_name of the member to request shutdown for (semantic slug, not display label)"
    ),
    "shutdown_member.force": (
        "Whether to force shutdown, default false. "
        "Use only when the member is stuck, unresponsive, "
        "or cannot complete a normal shutdown sequence"
    ),
    # ===== approve_plan ========================================================
    # approve_plan._desc lives in descs/en/member/approve_plan.md
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
    # approve_tool._desc lives in descs/en/member/approve_tool.md
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
    # list_members._desc lives in descs/en/member/list_members.md
    # ===== create_task ========================================================
    # create_task._desc lives in descs/en/task/create_task.md
    "create_task.tasks": "Task list — wrap single tasks in an array too",
    "create_task.task.task_id": "Custom task ID for dependency reference (auto-generated if omitted)",
    "create_task.task.title": "Task title — concise description of the goal",
    "create_task.task.content": "Task details including goals and acceptance criteria",
    # Both create_task variants expose assignee. Autonomous treats it as
    # optional; scheduled requires it.
    "create_task.task.assignee": (
        "Member name that carries this task; the member must already exist and must not be the leader. "
        "Optional in autonomous mode (omitted tasks enter the shared claim pool); required in scheduled mode "
        "because members never claim there"
    ),
    "create_task.task.depends_on": (
        "Prerequisite task IDs; may reference tasks created in this same call "
        "or existing tasks"
    ),
    "create_task.task.depended_by": (
        "Existing task IDs that should wait for this task (reverse dependency); must not "
        "reference tasks created in this same call — express in-batch edges with depends_on "
        "on the dependent task"
    ),
    "create_task.task.reviewer": (
        "Reviewer entries for this task (optional, may be several); each entry is an object "
        "with type/instruction. type: verifier / inspector / challenger. "
        "reviewer_id is auto-generated by the system — do not provide it."
    ),
    "create_task.task.reviewer_type": "Reviewer type: verifier / inspector / challenger",
    "create_task.task.reviewer_id": "Reviewer identifier (auto-generated, do not provide)",
    "create_task.task.reviewer_instruction": (
        "Supplementary instruction (verifier: verification guidance; "
        "inspector: scoring rubric; challenger: not needed)"
    ),
    "create_task.task.max_review_rounds": (
        "Review-round ceiling for this task's rework loop (optional, integer >= 1, requires "
        "'reviewer'); omitted uses the team default. Each failed verification sends the task "
        "back and opens a new round; beyond the ceiling the scheduler stops looping and "
        "escalates to you instead"
    ),
    # ===== view_task ===========================================================
    # view_task._desc lives in descs/en/task/view_task.md
    "view_task.action": (
        "View mode: 'list' (default, summary of all tasks), "
        "'get' (single task detail, requires task_id), "
        "'claimable' (pending tasks ready to claim), "
        "'in_review' (tasks assigned to you to verify, currently in_review)"
    ),
    "view_task.task_id": "Task ID — required when action=get, ignored otherwise",
    "view_task.status": (
        "Status filter for action=list only: "
        "pending/blocked/planning/in_progress/in_review/completed/cancelled. "
        "Omit to list all."
    ),
    # ===== update_task =========================================================
    # update_task._desc lives in descs/en/task/update_task.md
    "update_task.task_id": "Task ID to update, or '*' to cancel all tasks",
    "update_task.status": "Set to 'cancelled' to cancel the task",
    "update_task.title": "New task title",
    "update_task.content": "New task content",
    "update_task.assignee": (
        "member_name to assign or reassign this task to; the target must already exist and must not be the leader. "
        "A notification is sent to the assignee"
    ),
    "update_task.reviewer": (
        "Set this task's reviewer entries (empty list clears the verify gate); each entry is an "
        "object with type/instruction. type: verifier / inspector / challenger. "
        "reviewer_id is auto-generated — do not provide it."
    ),
    "update_task.reviewer_type": "Reviewer type: verifier / inspector / challenger",
    "update_task.reviewer_id": "Reviewer identifier (auto-generated, do not provide)",
    "update_task.reviewer_instruction": (
        "Supplementary instruction (verifier: verification guidance; "
        "inspector: scoring rubric; challenger: not needed)"
    ),
    "update_task.max_review_rounds": (
        "Set this task's review-round ceiling (integer >= 1; the task must carry reviewers, or set "
        "'reviewer' in the same call). Beyond the ceiling a failed verification escalates to you "
        "instead of looping rework"
    ),
    "update_task.add_blocked_by": (
        "Task IDs to add as new dependencies (this task will be blocked until those tasks complete)"
    ),
    "update_task.error_human_agent_locked_cancel": (
        "Task {task_id} is claimed by a human member still on the team; it "
        "cannot be cancelled. Use send_message to coordinate with that human. "
        "If they truly cannot continue, shutdown_member (without force) removes "
        "them from the team, after which the task can be cancelled or reassigned"
    ),
    "update_task.error_human_agent_locked_reassign": (
        "Task {task_id} is claimed by a human member still on the team; it "
        "cannot be reassigned to {new_assignee} and must be completed by that "
        "human. If they truly cannot continue, shutdown_member (without force) "
        "removes them from the team, after which the task can be reassigned"
    ),
    "update_task.error_human_agent_locked_edit": (
        "Task {task_id} is claimed by a human member still on the team; its "
        "title/content cannot be edited. Use send_message to coordinate with "
        "that human. If they truly cannot continue, shutdown_member (without "
        "force) removes them from the team, after which the task can be "
        "cancelled or reassigned"
    ),
    # ===== claim_task =========================================================
    # claim_task._desc lives in descs/en/task/claim_task.md
    "claim_task.task_id": "The ID of the task to claim or complete",
    "claim_task.status": "New status: 'claimed' (start work) or 'completed' (mark done)",
    # ===== member_complete_task ===============================================
    # member_complete_task._desc lives in descs/en/task/member_complete_task.md
    "member_complete_task.task_id": (
        "ID of the task to mark completed (must be a task the leader has assigned to you)"
    ),
    "member_complete_task.note": (
        "Optional completion note describing your result or any follow-up the team should know about"
    ),
    # ===== verify_task ========================================================
    # verify_task._desc lives in descs/en/task/verify_task.md
    "verify_task.task_id": "ID of the task to verify (must be a task assigned to you to review, currently in_review)",
    "verify_task.decision": (
        "Verdict: verifier/challenger cast 'pass'/'fail'; inspector casts a 0–1 float score (e.g. '0.85')"
    ),
    "verify_task.feedback": "Review feedback (directed to the author on a fail to guide rework; optional on pass)",
    # ===== send_message ========================================================
    # send_message._desc lives in descs/en/message/send_message.md
    "send_message.to": (
        'Recipient: member_name for a point-to-point DM (e.g. "backend-dev-1"), '
        "visible only to you and that member; "
        'array of member names (e.g. ["m1","m2"]) for multicast — same content sent '
        "as separate messages to each member, cost is linear in recipient count and "
        "MORE expensive than broadcast for the same audience, use only when truly needed "
        'and cannot mix with "*"/"user"; '
        '"user" (teammates only, to reply to the user; leader calls are rejected); '
        '"*" to broadcast on the team channel, visible to all members — one broadcast '
        "wakes every member for a full LLM turn, so cost scales linearly with team size; "
        "reserve it for announcements everyone must see and use it sparingly"
    ),
    "send_message.content": "Message content with clear action guidance or information",
    "send_message.summary": "5-10 word summary for message preview and logging",
    "send_message.error_leader_to_user": (
        "Leader cannot send_message to 'user'. Reply to the user directly in your assistant output instead."
    ),
    "send_message.error_content_too_long": (
        "'content' is too long ({actual} chars, limit {limit}): a body this size is an "
        "artifact, not a message. Write it to a file under the shared team workspace "
        ".team/ with write_file, then resend this message carrying only the file path "
        "plus a one- or two-sentence summary. Do not split the body across several "
        "messages to get around this limit."
    ),
    # ===== send_message_scheduled (scheduled-mode member variant) ==============
    # send_message_scheduled._desc lives in descs/en/message/send_message_scheduled.md
    # ``content`` / ``summary`` are reused verbatim from the send_message keys
    # above — only the recipient semantics differ, so only ``to`` is redefined.
    "send_message_scheduled.to": (
        'Recipient: only "leader" (a role name — the system delivers it to the real '
        "Leader; use it for progress, completion, blockers, reassignment requests) "
        'or "user" (only to reply when an incoming message came from the user). You '
        "cannot message other members; multicast and broadcast are unavailable in this mode"
    ),
    # NOTE: worktree tools (enter_worktree / exit_worktree) live in
    # ``openjiuwen.harness.tools.worktree`` and resolve their description
    # / param schema via ``harness.prompts.tools`` providers — no entries
    # in this dict.
    # ===== workspace_meta =====================================================
    # workspace_meta._desc lives in descs/en/workspace/workspace_meta.md
    "workspace_meta.action": (
        "Operation type: lock (acquire file lock), "
        "unlock (release file lock), "
        "locks (list all active locks), "
        "history (view file version history)"
    ),
    "workspace_meta.path": "Relative path of the target file (required for lock/unlock/history)",
    # ===== swarmflow / structured_output ======================================
    # swarmflow._desc lives in descs/en/workflow/swarmflow.md
    # structured_output._desc lives in descs/en/common/structured_output.md (dynamic schema, no fixed params)
    "swarmflow.script_path": (
        "Path to a swarmflow script file on disk — a Python module with a top-level META (pure literal) "
        "and async def run(args), whose body uses primitives imported via from swarmflow import "
        "agent()/parallel()/pipeline()/... Wired to execution alongside inline script; best for scripts "
        "already on disk or ones you iterate / resume repeatedly."
    ),
    "swarmflow.script": (
        "Self-contained inline swarmflow script source (avoids writing to disk first). Must begin with a "
        "top-level META (pure literal, no variables / calls / f-strings), then async def run(args), with the "
        "body using agent()/parallel()/pipeline()/phase() primitives. Wired to execution — prefer it for "
        "simple cases; the framework materialises the source under the workflow's journal directory before "
        "running."
    ),
    "swarmflow.name": (
        "Name of a saved / named swarmflow workflow, resolved to a self-contained script to run. "
        "Interface is in place; execution is coming — use script_path for now."
    ),
    "swarmflow.resume_id": (
        "The run_id of a prior run to resume. Unchanged agent() calls (same prompt + opts + schema) return "
        "their cached results instantly; only edited / new calls re-run (an upstream change cascades to "
        "invalidate downstream); same script + same args → full cache hit. Interface is in place; execution "
        "is coming — use script_path for now."
    ),
    "swarmflow.args": (
        "Optional argument passed to the script's async def run(args), as a **string** verbatim (e.g. a "
        "research question, a target path). The script parses it itself (json.loads inside run for "
        "structured input)."
    ),
    "swarmflow_worker.schema": (
        "You are a single-shot swarmflow worker. Read the task in the user message, "
        "do the work, then call the `structured_output` tool EXACTLY ONCE with the "
        "structured result conforming to its input schema. IMPORTANT: `structured_output` "
        "is the ONLY way to submit your result — if you do NOT call it, the task is "
        "considered FAILED and your text output is discarded. Do NOT write the result "
        "as plain text — it is only captured through the tool call. After calling "
        "`structured_output`, stop immediately."
    ),
    "swarmflow_worker.free": (
        "You are a single-shot swarmflow worker. Read the task in the user message, "
        "do the work, and return the answer as your final message."
    ),
    "structured_output.reminder": (
        "[IMPORTANT] You MUST submit your result by calling the `structured_output` tool. "
        "Do NOT write the result in your text. This is the ONLY way to submit — "
        "not calling the tool = task failure."
    ),
    # ===== async control tools (list / output / cancel) =======================
    # async_tasks_list._desc / async_task_output._desc / async_task_cancel._desc
    # live in descs/en/async_task/*.md
    "async_task_output.task_id": "Id of the background task to query (the task_id returned by the launching tool).",
    "async_task_output.block": (
        "Whether to block until the task reaches a terminal state: when true, poll until "
        "completed/failed or timeout; default false returns the current status immediately."
    ),
    "async_task_output.timeout": "Maximum wait in milliseconds when block=true (default 30000, capped at 600000).",
    "async_task_cancel.task_id": "Id of the background task to cancel.",
}
