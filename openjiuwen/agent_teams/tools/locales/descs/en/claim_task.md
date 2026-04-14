Claim or complete a task (Teammate only).

## When to Use

**Claim a task to start work:**
- After finding a pending, unclaimed task via view_task, set status=claimed
- Pick a task matching your domain expertise

**Mark tasks as completed:**
- When you have completed the work described in a task, set status=completed
- IMPORTANT: After completing, call view_task to find your next task

- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, or cannot finish, keep the task as claimed
- When blocked, notify the leader via send_message
- Never mark a task as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors

## Status Workflow

`pending` → `claimed` → `completed`

## Staleness

Read a task's latest state using view_task(action=get) before updating it.

## Examples

Claim a task:
{"task_id": "task-1", "status": "claimed"}

Complete a task:
{"task_id": "task-1", "status": "completed"}
