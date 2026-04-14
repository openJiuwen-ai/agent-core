Send a message to team members. Use for task assignment notifications, progress reports, escalation of blockers, or dependency coordination.

| `to` | |
|---|---|
| member name | Point-to-point message |
| `"user"` | Teammates only — when an incoming message has `from = user`, a teammate MUST use this tool with `to = "user"` to deliver its reply, since teammate plain text never reaches the user. The leader does NOT use this value: every leader plain-text output is shown directly to the user |
| `"*"` | Broadcast — expensive (linear in team size), use only for global decisions, constraint changes, or announcements everyone needs |

Teammate plain text output is NOT visible to other agents or to the user — teammates MUST call this tool to communicate. Leader plain text output IS shown to the user directly, so the leader does not need this tool to reply to the user. Messages from teammates are delivered automatically; you don't poll. Refer to members by name, never by internal ID. When relaying, don't quote the original — it's already rendered to the user.