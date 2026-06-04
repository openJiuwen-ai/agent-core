---
name: agent-team-member
description: >-
  Participate in an OpenJiuWen agent team as an external member. Use when this
  agent has been spawned into a team (the OPENJIUWEN_TEAM_JOIN environment
  variable is set) and needs to read its inbox, send messages to teammates,
  and claim / work / complete tasks via the `team-member` CLI.
---

# Agent Team Member

You are a member of an OpenJiuWen agent team. You collaborate with other
members (a leader and teammates) over a shared task board and mailbox. You
act through the `team-member` CLI; the team does not see your plain text
output — only what you send through the CLI.

The CLI reads its connection from the `OPENJIUWEN_TEAM_JOIN` environment
variable, which is already set in your environment.

## Coordination protocol

Run a simple loop:

1. **Read your inbox** — `team-member inbox`. It prints unread messages
   addressed to you plus the current task board. Reading marks messages read.
2. **Claim work** — find a pending task that fits you and claim it:
   `team-member claim <task_id>`. Use `team-member task get <task_id>` for the
   full requirement before you start.
3. **Do the work** — perform the actual task in your own tools / environment.
4. **Report and complete** — message the leader or a teammate with
   `team-member send <member> "<update>"`, then `team-member complete <task_id>`.
5. **Repeat** — read the inbox again for new messages or tasks.

Notes:

- Refer to members by name (e.g. `leader`, `dev-1`), never by internal id.
- A direct question in your inbox expects a reply via `team-member send`.
- An empty inbox is normal — it does not mean something is wrong.
- `team-member inbox --watch` blocks and streams the inbox as events arrive;
  use it instead of polling in a tight loop.

## Command reference

| Command | Purpose |
|---|---|
| `team-member inbox` | Show unread messages + task board (marks read). |
| `team-member inbox --watch` | Block and stream the inbox on each event. |
| `team-member send <to> "<text>"` | Direct message a member. |
| `team-member broadcast "<text>"` | Message the whole team (use sparingly). |
| `team-member task list [--status S]` | List tasks, optionally by status. |
| `team-member task claimable` | List pending tasks you can claim. |
| `team-member task get <id>` | Show one task's full detail. |
| `team-member claim <id>` | Claim a pending task. |
| `team-member complete <id>` | Complete a task you have claimed. |
| `team-member update <id> [--title T] [--content C]` | Edit a task (pending/blocked only). |
| `team-member members` | List team members. |

Exit code is `0` on success and `1` on failure; failure reasons are printed
to stderr.
