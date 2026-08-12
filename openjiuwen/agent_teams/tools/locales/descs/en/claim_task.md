Claim or complete a task (Teammate only).

## When to Use

**Claim a task to start work:**
- After finding a **pending** task whose **assignee is you** (or still unassigned) via view_task, set status=claimed
- If the Leader reserved the task for you at create time: `claim_task` on PENDING(assignee=you) **starts** it — it does **not** fail just because an assignee is already set
- **Do not** claim tasks whose assignee is another member (rejected)
- Prefer tasks assigned to you; for the unassigned pool, pick work matching your domain expertise
- **You can hold only ONE in-progress (in_progress) task at a time**: if you already have a task in flight, finish it before claiming another — otherwise the claim is refused

**Mark tasks as completed:**
- When you have completed the work described in a task, set status=completed
- IMPORTANT: After completing, call view_task to find your next task

- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, or cannot finish, keep the task as in_progress
- When blocked, notify the leader via send_message
- Never mark a task as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors

## Status Workflow

`pending` → `in_progress` → `completed`

## Staleness

Read a task's latest state using view_task(action=get) before updating it.

## Examples

Claim a task:
{"task_id": "task-1", "status": "claimed"}

Complete a task:
{"task_id": "task-1", "status": "completed"}
