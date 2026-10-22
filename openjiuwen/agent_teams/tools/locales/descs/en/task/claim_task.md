Claim or complete a task (Teammate only).

## When to Use

**Claim a task to start work:**
- After finding a pending, unclaimed task via view_task, set status=claimed
- Pick a task matching your domain expertise
- **You can hold only ONE in-progress (in_progress) task at a time**: if you already have a task in flight, finish it before claiming another — otherwise the claim is refused
- A successful claim lists the deliveries of this task's direct upstream; call `view_task(action='note', task_id=...)` for an upstream task's full note

**Mark tasks as completed:**
- When you have completed the work described in a task, set status=completed and fill in:
  - `summary`: a one-sentence completion summary — what you delivered and where the main artifact is. This is what the leader and your downstream see
  - `note`: the full completion detail — artifact paths, key decisions or follow-ups
- `summary` / `note` are your completion handoff — do **not** send_message a completion report afterwards; both take effect only with status=completed
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
{"task_id": "task-1", "status": "completed", "summary": "Login endpoint implemented in src/auth/login.py", "note": "JWT with a 2h expiry; unit tests in tests/test_login.py all pass"}
