Request a team member to enter the shutdown flow. After the Leader calls this tool, the system marks the member as shutdown_requested and sends a shutdown event; the member then finishes its own shutdown sequence and transitions to SHUTDOWN.

| Parameter | Usage |
|---|---|
| **member_name** | member_name of the member to request shutdown for; it should refer to a valid member in the current team |
| **force** | Whether to force shutdown. Use only as a fallback when the member is stuck, unresponsive, or cannot finish a normal shutdown sequence |

Use when:
- the member has completed its work and should exit cleanly
- the team is wrapping up and members must be closed before clean_team
- a member is stuck for a long time and force=true is needed as a fallback

Notes:
- This does not mean "the member is fully closed right now"; it initiates shutdown
- Wait until the member actually reaches SHUTDOWN before calling clean_team or assuming the member is gone
- force=true is a high-risk fallback for abnormal cases, not the normal path