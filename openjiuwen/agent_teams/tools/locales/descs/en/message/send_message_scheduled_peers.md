Message the Leader, another member, or the user. Use it for matters the Leader must know or decide, to escalate blockers, to request reassignment, and to work out upstream/downstream details directly with other members. Completion is handed over through the task's `summary` / `note` — do **not** use this tool to report completion or submission for review.

This team runs in **scheduled assignment mode** with **member-to-member messaging** switched on: the Leader still assigns tasks and the scheduler still performs every handoff, but you may contact other members directly to coordinate.

| Parameter | Meaning |
|---|---|
| `to="leader"` | Report to the Leader (point-to-point). This is a **role name**, not a concrete member_name — the system delivers it to the real Leader for you. Matters it must know or decide, blockers, and reassignment requests all go here |
| `to=<member name>` | Point-to-point message (DM), visible only to you and that member. Use it for **coordination** with upstream/downstream members — clarifying an interface, asking for or confirming artifact details. Do not use it to hand out work or to change task ownership behind the Leader's back |
| `targets=["m1","m2"]` | Multicast retained for old callers. Ordinary-team Agents send separate private messages with modes; global changes may use broadcast. |
| `to="*"` | Record a team broadcast. Recipients may receive, defer, or discard it. Success confirms recording only. If a particular member must acknowledge it, message them directly and request a reply. |
| `to="user"` | When an incoming message's `from` is `user`, you MUST reply through this tool — your plain text output never reaches the user |

Fill exactly one of `to` and `targets`; put a multicast array straight into `targets`, never JSON-encoded into `to`. A message to a member that has not started yet, is dormant or has failed brings that member up to handle it; a broadcast starts members that never ran but does not wake dormant ones.

## Ordinary-team private messages: choose by when the information is useful

Apply only to Agent recipients in ordinary teams. Messages to `user`, broadcasts, group chat, status queries take no mode. Set `mode` on every private send. Omission is accepted only for old callers, not the current Agent's calling convention. Idle recipients receive directly; do not query whether they are busy first.

Before sending, ask: **Would receiving this only after the current task ends be too late?** Choose by its effect on work, not labels such as "supplement" or "notification", tone, length, or whether the recipient is busy.

- **`steer`: needed before the current task ends.** Use when delay could cause errors, rework, continued blocking, use of an obsolete interface, or loss of the message's purpose. This includes changed requirements/acceptance criteria, corrections, dependency readiness, current interface/sample clarification, and necessary guidance before starting. It supplies context before the next model decision without forcibly stopping the current tool or requiring a restart from scratch. Example: "The corrected sample's row 5 ID changed; use the new file in the documentation you are writing."
- **`follow_up`: an independent later request.** Use only if the recipient can correctly finish the current task without knowing this message AND it will still be useful afterwards. Example: "After finishing the current report, produce a separate English abstract." Never defer information needed now merely to avoid disturbing a busy member; do not rely on a future steer to bring it into context.
- **`abort`: current execution itself must stop immediately.** Leader only, when correction cannot wait for the next model decision. Ordinary requirement changes, supplements and rework use steer; lack of a reply alone is not grounds to abort. It immediately stops execution and clears follow-ups. Its body is not executed and existing changes are not rolled back. Only a NEW direct Leader message resumes work. Do not combine "stop and report" in an abort; send a new message only when resumption or handover is intended, not as a polite acknowledgement. Members request a stop via steer to the Leader, stating the reason; the Leader decides.

Split current corrections and independent later requests into separate messages where practical. If they must be combined, use steer and state each part's timing. Early context inclusion does not mean later work should start immediately. If uncertain and delay could cause errors or rework, choose steer; do not repeatedly query progress just to select a mode. Do not repeat completion already handed over through the task board, or send thanks/acknowledgements requiring no action.

Within this tool's allowed recipients, contact several specific members separately with `to=<member name>` and choose each mode. Do not bypass selection with targets, even for a single recipient; targets remains for old callers. Global changes may still use `to="*"` without a sender mode; recipients decide delivery.

{{artifact_handoff_policy}}

Your plain text output is invisible to the Leader, other members, and the user — you MUST call this tool to communicate. Messages from the Leader and other members are delivered automatically; you don't poll. Refer to members by name, never by internal ID.
