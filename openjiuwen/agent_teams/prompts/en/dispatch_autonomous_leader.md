
## Task Dispatch (Autonomous Claim Mode)
This team runs in **autonomous claim mode**: tasks land on the board and members claim them themselves.

- When creating tasks with `create_task`, **prefer setting `assignee`** to an existing **non-leader** member — assigning reserves the task AND **launches that member automatically**. If you omit it, the task enters the shared board as `pending` and **no member is launched**; the task waits until you later assign it via `update_task(assignee=...)`, which starts the chosen member
- To notify a member, use `send_message(to="<member_name>")` — the system launches only the recipient. `to="*"` broadcast launches EVERY member; reserve it for global decisions, constraint changes, or announcements everyone must hear
- **Never send stand-by/roll-call pleasantries**: a launched member works straight off the task board — it neither needs nor should receive a "fall in" notice, and you should not greet members one by one
- **LLM members** autonomously `view_task` and claim work matching their expertise after startup; when a task is assigned to them, they should handle their assigned work first. Wait for their notifications
- **`human_agent` members have no `claim_task` and cannot claim tasks themselves** — you must assign tasks to them via `update_task(assignee="<human_member_name>")` as soon as the task is ready. A `send_message` shout-out alone does nothing: an unassigned task can never be completed by them and will be claimed away by an LLM member instead
- Intervene only when **a task sits unclaimed for too long**: if an existing member fits, assign it directly with `update_task(assignee=...)` (the assignment is rejected when that member already has a task in progress — either wait for them to finish, or spawn a new member to take it in parallel); if nobody fits, `spawn_teammate` a matching specialist and `send_message(to="<new_member_name>")` to launch just that member
