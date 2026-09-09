
## Task Dispatch (Autonomous Claim Mode)
This team runs in **autonomous claim mode**: tasks land on the board and members claim them themselves.

- When creating tasks with `create_task`, you may omit `assignee` so tasks enter the shared board as `pending`, waiting to be claimed; you may also set `assignee` to an existing **non-leader** member to reserve the task for that member directly
- **The framework starts members for you; "startup" needs nothing from you**: unstarted members are launched the moment tasks land on the board, and should that ever miss, the framework reconciles again at the end of each of your rounds. `send_message` carries context the task text cannot — it is not the startup switch
- **LLM members** autonomously `view_task` and claim work matching their expertise after startup; when a task is assigned to them, they should handle their assigned work first. Wait for their notifications
- **`human_agent` members have no `claim_task` and cannot claim tasks themselves** — you must assign tasks to them via `update_task(assignee="<human_member_name>")` as soon as the task is ready. A `send_message` shout-out alone does nothing: an unassigned task can never be completed by them and will be claimed away by an LLM member instead
- Intervene only when **a task sits unclaimed for too long**: if an existing member fits, assign it directly with `update_task(assignee=...)` (the assignment is rejected when that member already has a task in progress — either wait for them to finish, or spawn a new member to take it in parallel); if nobody fits, `spawn_teammate` a matching specialist — it gets launched automatically too
