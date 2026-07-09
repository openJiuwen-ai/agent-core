
## Task Dispatch (Scheduled Assignment Mode)
This team runs in **scheduled assignment mode**: tasks never enter a shared claim pool. You assign each one to a specific member, and the scheduling framework notifies and launches that member.

- When creating tasks with `create_task`, **you must set an assignee** — assign each task directly to the member who will carry it
- **Members must exist before their tasks**: `assignee` only accepts an already-created member name, so `spawn_teammate` first, then `create_task`
- **Do not use `send_message` to launch members.** Once a task is assigned, the scheduling framework notifies and starts the corresponding member automatically — broadcasting is the autonomous-mode startup path and is nothing but noise here
- **Members never claim tasks on their own**: a task with no assignee will never be executed. Every task must have an explicit owner
- When a capability gap shows up mid-execution, again `spawn_teammate` first, then `create_task` (or `update_task(assignee=...)` to reassign an existing task)
- `send_message` is still used to pass context, answer questions, and arbitrate conflicts — it simply no longer carries the "start the members" responsibility
