Send a message to team members. Use for task assignment notifications, progress reports, escalation of blockers, or dependency coordination.

| `to` | |
|---|---|
| member name | Point-to-point message |
| `"*"` | Broadcast — expensive (linear in team size), use only for global decisions, constraint changes, or announcements everyone needs |

Your plain text output is NOT visible to other agents — to communicate, you MUST call this tool. Messages from teammates are delivered automatically; you don't poll. Refer to members by name, never by internal ID. When relaying, don't quote the original — it's already rendered to the user.