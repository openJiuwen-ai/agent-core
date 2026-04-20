Assemble a team and register yourself as Leader. Call as soon as you have a goal — don't hesitate.

## Call Order
build_team → create_task → spawn_member → send_message(to="*").
No other team tool may be called before build_team.

## Task Design Principles
- Describe goals, not steps: content should contain goals, acceptance criteria, and constraints — not specific operations
- Single owner: each task may only be claimed by one teammate who owns delivery
- Coarse-grained: one task = one independently deliverable outcome
- Member autonomy: members create their own plans; Leader reviews via approve_plan

## Automatic Message Delivery
After sending messages, don't poll for replies or check task progress. The system proactively notifies you when new messages arrive or task states change. If nothing is pending, stop and wait for notifications.

## Member Idle State
Members won't reply immediately after startup — they need time to review tasks, create plans, and execute work. Idle is normal, not an error. Don't nudge, re-send, or shut down members. Only intervene when a member has made no progress for an extended period and hasn't reported any blocker.