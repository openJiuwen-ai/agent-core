---
name: agent-team-operator
description: >-
  Operate and control an OpenJiuWen agent team from OUTSIDE it (you are not a
  team member). Use when the OPENJIUWEN_TEAM_JOIN environment variable is set
  with scope "operator" and you need to hand out work, message members, inspect
  the task board / roster, and manage tasks via the `team-member` CLI.
---

# Agent Team Operator

You operate an OpenJiuWen agent team from outside it — you are a controller,
not a member. You act through the `team-member` CLI; you drive the team's work
by creating and managing tasks, messaging members, and watching the board.

The CLI reads its connection from the `OPENJIUWEN_TEAM_JOIN` environment
variable, which is already set in your environment.

## Control workflow

1. **See the state** — `team-member inbox` prints the mailbox + the task
   board; `team-member members` lists the roster; `team-member task list`
   shows tasks by status.
2. **Hand out work** — `team-member create_task "<title>" "<content>"`
   creates a task members can claim. Assign / edit / cancel with
   `team-member update`.
3. **Direct members** — `team-member send <member> "<text>"` for one member,
   `team-member broadcast "<text>"` for the whole team (use sparingly).
4. **Track progress** — re-read `task list` / `inbox`; `inbox --watch` streams
   updates instead of polling.

Notes:

- Refer to members by name; an operator is not itself on the roster.
- Launching new member *processes* is the team's local leader's job — an
  operator drives tasks, messages and the existing roster, not member startup.
- An empty inbox is normal.

## Command reference

| Command | Purpose |
|---|---|
| `team-member inbox [--watch]` | Show / stream messages + task board. |
| `team-member create_task "<title>" "<content>" [--task_id ID]` | Create a task. |
| `team-member send <to> "<text>"` | Direct message a member. |
| `team-member broadcast "<text>"` | Message the whole team. |
| `team-member task list [--status S]` | List tasks, optionally by status. |
| `team-member task claimable` | List pending tasks. |
| `team-member task get <id>` | Show one task's full detail. |
| `team-member claim <id>` / `complete <id>` | Claim / complete a task. |
| `team-member update <id> [--title T] [--content C]` | Edit a task. |
| `team-member members` | List team members. |

Exit code is `0` on success and non-zero on failure; failure reasons are
printed to stderr.
