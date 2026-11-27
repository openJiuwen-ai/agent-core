Send a message to team members. Use for task assignment notifications, progress reports, escalation of blockers, or dependency coordination.

**Group chat exception:** public replies must use `group_send_message`, not `send_message(to="user")`. The user-reply rules below apply only to ordinary teams. Private coordination between group members still uses this tool.

| Parameter | Meaning |
|---|---|
<!-- delivery:on -->
| `to=<member name>` | Point-to-point message (DM / direct message / private chat) — visible ONLY to you and that member, like a 1:1 private chat in any IM tool; no other teammate (and not even the leader) receives it. In ordinary teams, contact a subset through separate private messages with modes; global changes may use `to="*"` |
| `targets=["m1","m2"]` | Multicast retained for old callers. Ordinary-team Agents send separate private messages with modes; global changes may use broadcast. |
<!-- delivery:else -->
| `to=<member name>` | Point-to-point message (DM / direct message / private chat) — visible ONLY to you and that member, like a 1:1 private chat in any IM tool; no other teammate (and not even the leader) receives it. To reach a subset with the same content, use `targets`; to reach everyone, use `to="*"` |
| `targets=["m1","m2"]` | Multicast — the same content is sent as separate point-to-point messages to each listed member, each with its own read state. **Cost grows linearly with the number of recipients (one message row + one event per target), and for the same audience size this is MORE expensive than broadcast.** Use only when a specific subset of members must see the identical content AND broadcast is not appropriate; choose carefully. **When the recipients exactly cover every other team member (everyone but yourself), multicast is forbidden — you MUST broadcast with `to="*"` instead; a multicast over the whole team is just a more expensive broadcast and the tool will reject it.** The array cannot contain `"*"` or `"user"`. Treated as a whole-success only when every target succeeds; on partial failure the result lists who was delivered and who failed — do NOT resend to members already in the delivered list |
<!-- delivery:end -->
| `to="user"` | The **human outside the team** who commissioned this team's work — not a team member, and absent from the roster (not finding them there is normal). **Teammates only** — when an incoming message has `from = user`, a teammate MUST use this tool with `to="user"` to deliver its reply, since teammate plain text never reaches the user and skipping this call means no reply was sent at all. The leader does NOT use this value; leader calls with `to="user"` are rejected because every leader plain-text output is shown directly to the user |
<!-- delivery:on -->
| `to="*"` | Record a team broadcast. Recipients may receive, defer, or discard it. Success confirms recording only. If a particular member must acknowledge it, message them directly and request a reply. |
<!-- delivery:else -->
| `to="*"` | Broadcast to the team channel — like posting to a shared team channel in any IM tool, visible to every member. **One broadcast wakes EVERY member for a full LLM turn, so the cost scales linearly with team size (a 10-member team means 10 inference turns) — use it sparingly**: reserve it for global decisions, constraint changes, or announcements everyone must know. Anything relevant to only some members goes out as a DM or multicast; never broadcast as a casual "just letting everyone know" |
<!-- delivery:end -->

Provide exactly one of `to` or `targets`. Put a multicast array directly in `targets`; never JSON-encode an array into the `to` string.
<!-- delivery:on -->

## Ordinary-team private messages: choose by when the information is useful

Apply only to Agent recipients in ordinary teams. Messages to `user`, broadcasts, group chat take no mode. Set `mode` on every private send. Omission is accepted only for old callers, not the current Agent's calling convention. Idle recipients receive directly; do not query whether they are busy first.

Before sending, ask: **Would receiving this only after the current task ends be too late?** Choose by its effect on work, not labels such as "supplement" or "notification", tone, length, or whether the recipient is busy.

- **`steer`: needed before the current task ends.** Use when delay could cause errors, rework, continued blocking, use of an obsolete interface, or loss of the message's purpose. This includes changed requirements/acceptance criteria, corrections, dependency readiness, current interface/sample clarification, and necessary guidance before starting. It supplies context before the next model decision without forcibly stopping the current tool or requiring a restart from scratch. Example: "The corrected sample's row 5 ID changed; use the new file in the documentation you are writing."
- **`follow_up`: an independent later request.** Use only if the recipient can correctly finish the current task without knowing this message AND it will still be useful afterwards. Example: "After finishing the current report, produce a separate English abstract." Never defer information needed now merely to avoid disturbing a busy member; do not rely on a future steer to bring it into context.
- **`abort`: current execution itself must stop immediately.** Leader only, when correction cannot wait for the next model decision. Ordinary requirement changes, supplements and rework use steer; lack of a reply alone is not grounds to abort. It immediately stops execution and clears follow-ups. Its body is not executed and existing changes are not rolled back. Only a NEW direct Leader message resumes work. Do not combine "stop and report" in an abort; send a new message only when resumption or handover is intended, not as a polite acknowledgement. Members request a stop via steer to the Leader, stating the reason; the Leader decides.

Split current corrections and independent later requests into separate messages where practical. If they must be combined, use steer and state each part's timing. Early context inclusion does not mean later work should start immediately. If uncertain and delay could cause errors or rework, choose steer; do not repeatedly query progress just to select a mode. Do not repeat completion already handed over through the task board, or send thanks/acknowledgements requiring no action.

Within this tool's allowed recipients, contact several specific members separately with `to=<member name>` and choose each mode. Do not bypass selection with targets, even for a single recipient; targets remains for old callers. Global changes may still use `to="*"` without a sender mode; recipients decide delivery.
<!-- delivery:else -->
<!-- delivery:end -->

{{artifact_handoff_policy}}

Teammate plain text output is NOT visible to other agents or to the user — teammates MUST call this tool to communicate. Leader plain text output IS shown to the user directly, so the leader does not need this tool to reply to the user. Messages from teammates are delivered automatically; you don't poll. Refer to members by name, never by internal ID. When relaying, don't quote the original — it's already rendered to the user.
<!-- delivery:on -->

<!-- delivery:else -->
<!-- delivery:end -->
