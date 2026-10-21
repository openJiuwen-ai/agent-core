Message the Leader, another member, or the user. Use it for matters the Leader must know or decide, to escalate blockers, to request reassignment, and to work out upstream/downstream details directly with other members. Completion is handed over through the task's `summary` / `note` — do **not** use this tool to report completion or submission for review.

This team runs in **scheduled assignment mode** with **member-to-member messaging** switched on: the Leader still assigns tasks and the scheduler still performs every handoff, but you may contact other members directly to coordinate.

| Parameter | Meaning |
|---|---|
| `to="leader"` | Report to the Leader (point-to-point). This is a **role name**, not a concrete member_name — the system delivers it to the real Leader for you. Matters it must know or decide, blockers, and reassignment requests all go here |
| `to=<member name>` | Point-to-point message (DM), visible only to you and that member. Use it for **coordination** with upstream/downstream members — clarifying an interface, asking for or confirming artifact details. Do not use it to hand out work or to change task ownership behind the Leader's back |
| `targets=["m1","m2"]` | Multicast — the same content sent separately to each member; cost grows linearly with recipient count, so use it only when several members genuinely need the same content. The array may contain `"leader"` but not `"*"` / `"user"`; when the recipients are exactly every other member, broadcast instead. On partial failure the result lists delivered and failed members — do not resend to the delivered ones |
| `to="*"` | Broadcast to every member — **one broadcast wakes every member for a full LLM turn, so cost scales with team size; use it sparingly**, only for matters everyone must know |
| `to="user"` | When an incoming message's `from` is `user`, you MUST reply through this tool — your plain text output never reaches the user |

Fill exactly one of `to` and `targets`; put a multicast array straight into `targets`, never JSON-encoded into `to`. A message to a member that has not started yet or is dormant stays in its mailbox and is read when it next starts work.

{{artifact_handoff_policy}}

Your plain text output is invisible to the Leader, other members, and the user — you MUST call this tool to communicate. Messages from the Leader and other members are delivered automatically; you don't poll. Refer to members by name, never by internal ID.
