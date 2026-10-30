## Inline Task Members

- Besides `assignee`, a task's owner can be **declared inline** with `member` (one of the two). Resident members: `spawn_teammate` first, then `create_task`; a specialist that exists for one task: declare it inline with `member`, giving its post-work retirement policy `lifetime` (`shutdown` = auto shutdown / `dormant` = auto dormant)
- When a capability gap shows up mid-execution, you can also declare the owner inline with `create_task(member=...)`

Inline members retire automatically: once every task on a member's board is terminal and it has nothing unread, the scheduling framework shuts it down or puts it to sleep per its `lifetime`.

- **A dormant member stays on the roster** (`list_members` shows `status=dormant`); it processes no events and consumes nothing
- **Addressing it wakes it**: `send_message(to=<member>)`, `targets` naming it, or a new task assigned to it (`create_task(assignee=...)` / `update_task(assignee=...)`) — the framework wakes it on its existing context; once done it goes dormant again
- **Broadcasts never wake it**: `send_message(to="*")` only lands in its mailbox, read the next time it is addressed
- To remove a dormant member for good, call `shutdown_member` on it once more; a temporary team still needs every member SHUTDOWN before `clean_team`
