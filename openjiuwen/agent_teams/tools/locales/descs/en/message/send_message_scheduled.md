Report to the Leader, or reply to the user. Use it for progress updates and completion reports, to escalate blockers, or to request reassignment.

This team runs in **scheduled assignment mode**: you never talk to other members directly. Every collaboration need goes through the Leader.

| `to` value | Meaning |
|---|---|
| `"leader"` | Report to the Leader (point-to-point). This is a **role name**, not a concrete member_name — the system delivers it to the real Leader for you. Progress, completion, blockers, and reassignment requests all go here |
| `"user"` | When an incoming message's `from` is `user`, you MUST reply through this tool — your plain text output never reaches the user |

**You cannot message other members, multicast, or broadcast.** When you need another member's help, tell the Leader and let it coordinate or reassign.

{{artifact_handoff_policy}}

Your plain text output is invisible to both the Leader and the user — you MUST call this tool to communicate. Messages from the Leader are delivered automatically; you don't poll. Refer to members by name, never by internal ID.
