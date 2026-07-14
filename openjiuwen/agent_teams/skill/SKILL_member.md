---
name: agent-team-member
description: >-
  Participate in an OpenJiuWen agent team as a first-class member. Use when
  this agent has been spawned into a team (the OPENJIUWEN_TEAM_JOIN environment
  variable is set with scope "member") and needs to read its inbox, send
  messages to teammates, and claim / work / complete tasks via the
  `team-member` CLI. The CLI exposes the same team tools a native teammate
  uses, so behaviour and result text match an in-process member exactly.
---

# Agent Team Member

You are a member of an OpenJiuWen agent team. You collaborate with other
members (a leader and teammates) over a shared task board and mailbox. You
act through the `team-member` CLI; the team does not see your plain text
output — only what you send through the CLI's team tools.

The CLI reads its connection from the `OPENJIUWEN_TEAM_JOIN` environment
variable, which is already set in your environment. The team tools are the
real `view_task` / `claim_task` / `send_message` tools — identical to what a
native in-process teammate calls.

## Coordination protocol

Run a simple loop:

1. **Read your inbox** — `team-member inbox`. It prints unread messages
   addressed to you plus the current task board. Reading marks messages read.
2. **Claim work** — find a pending task that fits you and claim it:
   `team-member claim_task <task_id> claimed`. Use
   `team-member view_task --action get --task_id <id>` for the full
   requirement before you start.
3. **Do the work** — perform the actual task in your own tools / environment.
4. **Report and complete** — message the leader with
   `team-member send_message <member> "<update>"`, then mark the task done
   with `team-member claim_task <task_id> completed`.
5. **Repeat** — read the inbox again for new messages or tasks.

Notes:

- Refer to members by name (e.g. `leader`, `dev-1`), never by internal id.
- A direct question in your inbox expects a reply via `team-member send_message`.
- An empty inbox is normal — it does not mean something is wrong.
- `team-member inbox --watch` blocks and streams the inbox as events arrive;
  use it instead of polling in a tight loop.
- Completing a task is `claim_task ... completed` — there is no separate
  "complete" command; the claim tool carries the status.

## Command reference

| Command | Purpose |
|---|---|
| `team-member inbox` | Show unread messages + task board (marks read). |
| `team-member inbox --watch` | Block and stream the inbox on each event. |
| `team-member view_task [--action list\|get\|claimable] [--task_id ID] [--status S]` | View tasks (default: list). |
| `team-member claim_task <id> claimed` | Claim a pending task. |
| `team-member claim_task <id> completed` | Complete a task you have claimed. |
| `team-member send_message <to> "<text>"` | Message a member (`*` to broadcast). |

Exit code is `0` on success and non-zero on failure; failure reasons are
printed to stderr.
