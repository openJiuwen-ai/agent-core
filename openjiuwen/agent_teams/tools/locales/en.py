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
    "checkpoint.duplicate": (
        "⚠️ Snapshot save FAILED: the name '{name}' is already taken by "
        "{created_by} ({description}). Your snapshot was NOT created, so a "
        "later fork will not inherit your context. Call checkpoint AGAIN with "
        "a different name (e.g. '{name}-v2', or append a number / business "
        "suffix) to actually save it — do not substitute a plain-text "
        "explanation for the call. Only skip re-calling if your goal was to "
        "inherit that existing snapshot rather than save your own."
    ),
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
    "spawn_teammate.model_name_group": (
        "Optional. Set only when the user explicitly asks for this member to use a "
        "specific model, and copy the value verbatim from selectable_models below. "
        "Omit it when the user did not mention a model; the model group chooses.\n"
        "Before setting it, compare the user's wording with each entry in "
        "selectable_models (ignoring case, spaces and hyphens):\n"
        "- Exactly equal to one entry: that entry is the only match; use it.\n"
        "- Otherwise, every entry whose name contains the user's wording is a match. "
        "Use it when there is exactly one; when there are several, call ask_user to "
        "ask the user to choose among them, and never pick the shortest or "
        "closest-looking one yourself. For example, if the user says \"use GLM\" "
        "and the list has GLM-5.3 and GLM-5.3-Flash, you must ask.\n"
        "- No match: call ask_user to tell the user which models are selectable, "
        "and never substitute another model"
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
    "spawn_teammate.fork_mode": (
        "Which side of the checkpoint to keep. Options: 'full' (the source's "
        "whole context, default for fork=true), 'before' (messages before the "
        "checkpoint, default for a named fork), 'after' (messages from the "
        "checkpoint onward), 'keep_before_compact_after' (keep before, compress "
        "after into a summary), 'keep_after_compact_before' (keep after verbatim, "
        "compress before into a summary). Only effective with a named "
        "checkpoint fork"
    ),
    "spawn_teammate.cli_agent": (
        "Optional. Run this teammate as a third-party CLI agent instead of a local LLM: the identifier of a CLI "
        "kind pre-declared in spec.external_cli_agents (e.g. 'claude' or 'codex'). The launch command, working "
        "directory and MCP injection all live in that entry; this field only references it by name. With it set, "
        "isolation / permissions / fork are unavailable"
    ),
    "spawn_teammate.model_name_external_cli": (
        "With cli_agent set: fill only when the user explicitly names the model for this third-party agent, never "
        "choose or infer one; it must be a team model pool endpoint compatible with the CLI's protocol. Omit it so "
        "the CLI uses its own default model; a model the CLI offers on its own login goes in "
        "external_cli.builtin_model"
    ),
    "spawn_teammate.external_cli": (
        "Optional object, only together with cli_agent (passing it without cli_agent is an error): the external "
        "CLI member's authentication fallback and built-in model choice"
    ),
    "spawn_teammate.external_cli.fallback_model_name": (
        "Authentication fallback model. Only valid for the cli_agent kinds listed under "
        "compatible_fallback_models in the catalog below; any other cli_agent has no authentication fallback "
        "and the field must be omitted. Required when that cli_agent has a compatible model: select it from the "
        "team model pool according to the model API protocol supported by this third-party agent. "
        "Prefer the current model when it is present in the pool and its protocol is compatible; when the "
        "current model is absent from the pool or its protocol is incompatible, select another compatible model. "
        "Never invent an unavailable or incompatible model. It is used for automatic fallback "
        "when the agent uses its own default model but authentication is unavailable. It applies only to "
        "authentication failures explicitly reported by the runtime. Omit it when the team model pool has "
        "no compatible model; the agent can then use its own default model without automatic fallback"
    ),
    "spawn_teammate.external_cli.builtin_model": (
        "Optional. A built-in model from this cli_agent's entry in the catalog below, running on the CLI's "
        "own login (e.g. a subscription); pick it to fit the member's workload. Mutually exclusive with "
        "model_name. Omit to let the CLI use its own default model"
    ),
    "spawn_teammate.external_cli.effort": (
        "Optional. Reasoning effort, one of the chosen builtin_model's efforts; requires builtin_model. "
        "Omit to take the model's default_effort"
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
    # ===== spawn_passive_human =================================================
    # spawn_passive_human._desc lives in descs/en/member/spawn_passive_human.md
    "spawn_passive_human.member_name": (
        "[PUBLIC] Unique name for the passive human member (semantic slug, "
        "e.g. product-owner, DNS-label kebab-case). **First character must be a "
        "lowercase ASCII letter (a-z); the rest may be lowercase letters, "
        "digits (0-9) or hyphen (-)** — no uppercase, underscore, whitespace, "
        "CJK or any other non-ASCII characters. Serves as the primary "
        "identifier and routing key; must be unique within the team"
    ),
    "spawn_passive_human.display_name": (
        "[PUBLIC] Human-readable display label for the passive human member "
        "(e.g. 'Product Owner'); purely presentational, not used for routing. "
        "Injected into every other member's system prompt and returned by "
        "list_members — do not put private content here"
    ),
    "spawn_passive_human.desc": (
        "[PUBLIC] Role profile and responsibilities of the passive human "
        "member, used for display and description persistence and injected "
        "into other members' system prompts / returned by list_members. This "
        "member has no internal avatar: the real human communicates and "
        "relays tool calls through their external channel, and may be "
        "assigned tasks"
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
    # ===== view_experts ========================================================
    # view_experts._desc lives in descs/en/expert/view_experts.md
    "view_experts.action": "list (default) lists the installed single experts; get shows one expert in full",
    "view_experts.query": (
        "list only: case-insensitive substring filter over package name, display name and description"
    ),
    "view_experts.name": "Required for get: the expert's package name (name in the list, not the display name)",
    # ===== view_expert_groups ==================================================
    # view_expert_groups._desc lives in descs/en/expert/view_expert_groups.md
    "view_expert_groups.action": (
        "list (default) lists the installed expert groups; get shows one group and its members"
    ),
    "view_expert_groups.query": (
        "list only: case-insensitive substring filter over group name, display name and description"
    ),
    "view_expert_groups.capabilities": "list only: keep only groups that have all listed capability tags",
    "view_expert_groups.name": "Required for get: the expert group name",
    "view_expert_groups.member": "get only, optional: expand just this member of the group",
    # ===== spawn_expert ========================================================
    # spawn_expert._desc lives in descs/en/expert/spawn_expert.md
    "spawn_expert.expert": (
        "Required. A single expert's package name; with group, a member name of that group (not leader)"
    ),
    "spawn_expert.group": "Expert group name; omit when expert is a single expert. Required for a group member",
    "spawn_expert.member_name": (
        "[public] Unique member name in the team (semantic slug such as finance-1 or clip-2, DNS-label kebab-case). "
        "**Must start with a lowercase ASCII letter (a-z), followed only by lowercase letters, digits (0-9) or "
        "hyphens (-)**; one expert can be spawned several times under different names"
    ),
    "spawn_expert.display_name": (
        "[public] Optional. Defaults to the expert's display name; \" (member_name)\" is appended when a teammate "
        "already uses that display name"
    ),
    "spawn_expert.desc": (
        "[public] Optional. Added public description, appended after the expert's own description (never replaces "
        "it). Say how this instance differs from its siblings, e.g. \"covers shots 1-10\"; no private information"
    ),
    "spawn_expert.prompt": (
        "[private] Optional. Added standing agreement, appended after the expert's persona (never replaces it). "
        "Do not restate the persona or put concrete tasks here"
    ),
    "spawn_expert.model_name": (
        "Optional. Fill only when the user names a model; never pick or infer one yourself. Native experts follow "
        "spawn_teammate, external-runtime experts follow spawn_teammate with cli_agent (a team pool endpoint "
        "compatible with the CLI's protocol)"
    ),
    "spawn_expert.external_cli": (
        "Optional object, only for external-runtime experts (runtime other than native); passing it for a native "
        "expert is an error. Fields mean and validate like spawn_teammate.external_cli"
    ),
    "spawn_expert.external_cli.builtin_model": (
        "Optional. A built-in model from the expert's CLI kind entry in the catalog below, run on the CLI's own "
        "login; mutually exclusive with model_name"
    ),
    "spawn_expert.isolation": (
        "Optional isolation mode, as in spawn_teammate: set 'worktree' only when the user asks for it or the member "
        "must modify repository files in an isolated checkout. Not supported for external-runtime experts"
    ),
    "spawn_expert.permissions": (
        "Optional, as spawn_teammate.permissions (tighten only). Not supported for external-runtime experts"
    ),
    # ===== set_member_model ====================================================
    # set_member_model._desc lives in descs/en/member/set_member_model.md
    "set_member_model.member_name": (
        "member_name of the external CLI member to switch (semantic slug, not display label)"
    ),
    "set_member_model.model": (
        "Optional. The new built-in model, from the member's cli_agent entry in the catalog below; omit to "
        "keep the current model and change only effort"
    ),
    "set_member_model.effort": (
        "Optional. The new reasoning effort, one of the chosen model's efforts; omitted with model set it "
        "takes the model's default_effort, omitted without model it stays unchanged"
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
    # ``assignee`` reads differently per create_task variant: autonomous has
    # no inline member, so its string must not mention one. The scheduled
    # strings live under the ``create_task_scheduled`` namespace below.
    "create_task.task.assignee": (
        "Member name that carries this task (optional); the member must already exist (spawn_teammate first) "
        "and must not be the leader. Omit it to put the task on the shared claim pool for members to claim"
    ),
    "create_task_scheduled.task.assignee": (
        "Member name that carries this task (one of assignee / member); the member must already exist (it may "
        "also be a member declared inline via 'member' by another task of the same call) and must not be the "
        "leader. Scheduled mode requires an owner because members never claim there"
    ),
    "create_task_scheduled.task.member": (
        "Owner created together with the task (one of assignee / member): same fields as spawn_teammate "
        "(member_name / display_name / desc / prompt / model_name) plus a required lifetime; when the team declares "
        "external CLI kinds, cli_agent runs it on an external CLI instead. The member becomes the "
        "task's assignee and is started by the scheduling framework; once every task on its board is terminal it "
        "retires per lifetime"
    ),
    "create_task_scheduled.task.member.expert": (
        "Optional. Run this member as an installed expert template: a single expert's package name, or, with "
        "group, a member name of that group (not leader). With it, display_name / desc may be omitted (the "
        "expert's own are used) and desc / prompt are appended after the expert's description / persona. "
        "Experts with an external CLI runtime may be declared inline too; their model arguments go in external_cli, "
        "with spawn_expert's rules"
    ),
    "create_task_scheduled.task.member.cli_agent": (
        "Optional, exclusive with expert. Run this member as a third-party CLI agent, as spawn_teammate.cli_agent: "
        "the value must be a CLI kind declared in spec.external_cli_agents; model_name is the team model pool "
        "endpoint the CLI uses"
    ),
    "create_task_scheduled.task.member.external_cli": (
        "Optional object, only for external CLI members (cli_agent set, or expert is an external-runtime expert); "
        "passing it otherwise is an error. Fields mean and validate like spawn_teammate.external_cli"
    ),
    "create_task_scheduled.task.member.builtin_model": (
        "Optional. A built-in model from the member's CLI kind entry in the catalog below, run on the CLI's own "
        "login; mutually exclusive with model_name"
    ),
    "create_task_scheduled.task.member.group": (
        "Optional, requires expert. The expert group name, making expert a member name of that group; when "
        "this team was assembled from that group, the new member matches its predefined member of that name"
    ),
    "create_task_scheduled.task.member.lifetime": (
        "Retirement policy: shutdown = shut down automatically once its tasks are all done (terminal, the name "
        "cannot be reused); dormant = go dormant automatically (stays on the roster, processes no events) and wakes "
        "only when addressed directly (send_message(to=<member>) or a new task assigned to it)"
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
        "'note' (a task's full completion summary and note, requires task_id), "
        "'claimable' (pending tasks ready to claim), "
        "'in_review' (tasks assigned to you to verify, currently in_review)"
    ),
    "view_task.task_id": "Task ID — required when action=get or note, ignored otherwise",
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
    "claim_task.summary": (
        "Only with status=completed: one-sentence completion summary — what you delivered and where the main "
        "artifact is; this is what the leader and your downstream see; when the deliverable itself is short "
        "(one sentence, a number, a verdict), put it in the summary itself rather than only a file path"
    ),
    "claim_task.note": (
        "Only with status=completed: the full completion detail — artifact paths, key decisions or follow-ups"
    ),
    "claim_task.fields_ignored": "summary / note only take effect with status=completed; ignored for this claim.",
    "claim_task.upstream_header": "Upstream deliveries (this task's direct dependencies):",
    "claim_task.upstream_note_hint": (
        "For an upstream task's full completion note, call `view_task(action='note', task_id=...)`."
    ),
    # ===== member_complete_task ===============================================
    # member_complete_task._desc lives in descs/en/task/member_complete_task.md
    "member_complete_task.task_id": (
        "ID of the task to mark completed (must be a task the leader has assigned to you)"
    ),
    "member_complete_task.summary": (
        "One-sentence completion summary: what you delivered and where the main artifact is; shown in the "
        "leader digest and in downstream upstream lists; when the deliverable itself is short "
        "(one sentence, a number, a verdict), put it in the summary itself rather than only a file path"
    ),
    "member_complete_task.note": (
        "Full completion detail: artifact paths, key decisions or follow-ups the team should know about; "
        "reviewers and view_task(action='note') read it in full"
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
        'Single recipient: member_name for a point-to-point DM (e.g. "backend-dev-1"), '
        "visible only to you and that member; "
        '"user" (teammates only, to reply to the user; leader calls are rejected); '
        '"*" to broadcast on the team channel, visible to all members — one broadcast '
        "wakes every member for a full LLM turn, so cost scales linearly with team size; "
        "reserve it for announcements everyone must see and use it sparingly. "
        "For multicast, leave this field unset and use targets"
    ),
    "send_message.targets": (
        'Multicast recipient array (e.g. ["m1","m2"]): the same content is sent as '
        "separate messages to each member; cost grows linearly with recipient count and "
        'is higher than broadcast for the same audience. Cannot contain "*" or "user". '
        "Do not provide together with to"
    ),
    "send_message.content": "Message content with clear action guidance or information",
    "send_message.summary": "5-10 word summary for message preview and logging",
    "send_message.error_leader_to_user": (
        "Leader cannot send_message to 'user'. Reply to the user directly in your assistant output instead."
    ),
    "send_message.error_content_too_long": (
        "'content' is too long ({actual} chars, limit {limit}): a body this size is an "
        "artifact, not a message. Write it to a file under the shared team deliverables "
        "directory (see the \"Final deliverables directory\" under \"Team Shared Workspace\" "
        "in the team info block) with write_file, then resend this message carrying only "
        "the file path plus a one- or two-sentence summary. Do not split the body across "
        "several messages to get around this limit."
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
    # ===== send_message_scheduled_peers (scheduled member, peers opened) =======
    # send_message_scheduled_peers._desc lives in
    # descs/en/message/send_message_scheduled_peers.md (F_126). ``content`` /
    # ``summary`` reuse the send_message keys; ``to`` / ``targets`` gain the
    # "leader" role word.
    "send_message_scheduled_peers.to": (
        'Single recipient: "leader" (a role name — the system delivers it to the real '
        "Leader), another member's member_name (a point-to-point DM for coordination), "
        '"user" (only to reply when an incoming message came from the user), or "*" to '
        "broadcast to every member — one broadcast wakes every member for a full LLM "
        "turn, so use it sparingly. For multicast, leave this field unset and use targets"
    ),
    "send_message_scheduled_peers.targets": (
        'Multicast recipient array (e.g. ["leader","m2"]): the same content is sent as '
        "separate messages to each member; cost grows linearly with recipient count. "
        'May contain "leader"; cannot contain "*" or "user". Do not provide together with to'
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
        "The run_id of a prior run to resume or control. Passed alone it resumes: unchanged agent() calls "
        "(same prompt + opts + schema) return their cached results instantly; only edited / new calls re-run "
        "(an upstream change cascades to invalidate downstream); same script + same args → full cache hit. "
        "Combined with the action param it controls a live run — action='pause' pauses, 'resume' resumes, "
        "'stop' stops (keeps the session alive)."
    ),
    "swarmflow.action": (
        "Control action on an existing run: 'pause' to pause, 'resume' to resume, 'stop' to stop "
        "(requires resume_id to be passed as well)."
    ),
    "swarmflow.args": (
        "Optional argument passed to the script's async def run(args), as a **string** verbatim (e.g. a "
        "research question, a target path). The script parses it itself (json.loads inside run for "
        "structured input)."
    ),
    "swarmflow_worker.schema": (
        "You are a single-shot swarmflow worker. Read the task in the user message, "
        "do the work, then call the `structured_output` tool EXACTLY ONCE with the "
        "structured result conforming to its input schema: provide every required "
        "property at every nesting level (including each item inside arrays), with "
        "the exact property names and structure the schema defines. IMPORTANT: "
        "`structured_output` is the ONLY way to submit your result — if you do NOT "
        "call it, the task is considered FAILED and your text output is discarded. "
        "Do NOT write the result as plain text — it is only captured through the "
        "tool call. After calling `structured_output`, stop immediately."
    ),
    "swarmflow_worker.free": (
        "You are a single-shot swarmflow worker. Read the task in the user message, "
        "do the work, and return the answer as your final message."
    ),
    "structured_output.reminder": (
        "[IMPORTANT] You MUST submit your result by calling the `structured_output` tool. "
        "Do NOT write the result in your text. The arguments must strictly conform to "
        "the tool's input schema: every required property at every nesting level, with "
        "the exact names and structure. This is the ONLY way to submit — not calling "
        "the tool = task failure."
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
