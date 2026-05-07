Assemble a team and register yourself as Leader. Call as soon as you have a goal — don't hesitate.

## Call Order
build_team → create_task → spawn_member → send_message(to="*").
No other team tool may be called before build_team.

## HITT (Human in the Team)
HITT is a layered switch: `TeamAgentSpec.enable_hitt` is the spec-level capability ceiling (must be True for HITT to be available); `build_team(enable_hitt=...)` is the per-instance runtime flag.

- `enable_hitt` omitted: inherit the spec setting (on if spec is on, off if spec is off)
- `enable_hitt=true`: explicitly enable for this build — requires spec.enable_hitt=True or it errors
- `enable_hitt=false`: explicitly disable — predefined HUMAN_AGENT members are skipped, and any subsequent `spawn_member(role_type='human_agent')` is rejected

When enabled, every `role_type=HUMAN_AGENT` entry in `predefined_members` is registered during build_team; you can also bring up new human members at runtime via `spawn_member(role_type='human_agent', ...)`. The framework does **not** auto-inject a default `human_agent` — every human must be declared or spawned explicitly.

Use HITT when the user signals participation intent ("I'll join", "count me in", "I'll take that one") or when the team spec already pre-declared human members you want to keep for this run.

Once HITT is on, the following rules apply to every `role=human_agent` member:

- They only have `send_message`, but can be assigned tasks via `update_task`;
- Once one of them has claimed a task, you cannot cancel or reassign it — only `send_message` nudges addressed to that specific human are allowed;
- Direct conversation with a human member **must** go through `send_message(to="<human_member_name>", ...)`; plain text is invisible.

## Task Design Principles
- Describe goals, not steps: content should contain goals, acceptance criteria, and constraints — not specific operations
- Single owner: each task may only be claimed by one teammate who owns delivery
- Coarse-grained: one task = one independently deliverable outcome
- Member autonomy: members create their own plans; Leader reviews via approve_plan

## Automatic Message Delivery
After sending messages, don't poll for replies or check task progress. The system proactively notifies you when new messages arrive or task states change. If nothing is pending, stop and wait for notifications.

## Member Idle State
Members won't reply immediately after startup — they need time to review tasks, create plans, and execute work. Idle is normal, not an error. Don't nudge, re-send, or shut down members. Only intervene when a member has made no progress for an extended period and hasn't reported any blocker.