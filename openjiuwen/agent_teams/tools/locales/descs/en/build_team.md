Assemble a team and register yourself as Leader. Call as soon as you have a goal — don't hesitate.

## Call Order
build_team → create_task → spawn_member → send_message(to="*").
No other team tool may be called before build_team.

## HITT (Human in the Team)
Passing `enable_hitt=true` registers a single default `human_agent` member as a first-class teammate, equal in standing with you and the teammates. Use it when the user signals participation intent ("I'll join", "count me in", "I'll take that one").

**Multiple distinct human members**: declare multiple `TeamMemberSpec(role_type=HUMAN_AGENT, member_name="...")` entries in the team spec (e.g. `human_designer`, `human_pm`). In that case `enable_hitt` is optional — every declared human-agent spec is registered.

Once HITT is on (one or many human members), the following rules apply to every `role=human_agent` member:

- They only have `send_message`, but can be assigned tasks via `update_task`;
- Once one of them has claimed a task, you cannot cancel or reassign it — only `send_message` nudges addressed to that specific human are allowed;
- Direct conversation with a human member **must** go through `send_message(to="<human_member_name>", ...)`; plain text is invisible.

HITT is decided once at build_team; you cannot add a human member after the team is built.

## Task Design Principles
- Describe goals, not steps: content should contain goals, acceptance criteria, and constraints — not specific operations
- Single owner: each task may only be claimed by one teammate who owns delivery
- Coarse-grained: one task = one independently deliverable outcome
- Member autonomy: members create their own plans; Leader reviews via approve_plan

## Automatic Message Delivery
After sending messages, don't poll for replies or check task progress. The system proactively notifies you when new messages arrive or task states change. If nothing is pending, stop and wait for notifications.

## Member Idle State
Members won't reply immediately after startup — they need time to review tasks, create plans, and execute work. Idle is normal, not an error. Don't nudge, re-send, or shut down members. Only intervene when a member has made no progress for an extended period and hasn't reported any blocker.