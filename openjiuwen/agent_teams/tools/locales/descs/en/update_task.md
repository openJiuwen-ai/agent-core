Update task content, dependencies, assignee, or cancel tasks (Leader only).

## When to Use

**Update task content:**
- Adjust task scope or add details based on member feedback
- Pass title and/or content to update. If the task is already claimed, the system automatically cancels the assignee and resets the task

**Assign a task:**
- Set assignee to a member name to assign the task. Only works when the task has no current assignee
- The system sends a notification to the assigned member

**Add dependencies:**
- Pass add_blocked_by with task IDs to add new prerequisite dependencies
- If the task was pending, it automatically becomes blocked until all dependencies complete

**Cancel a task:**
- Set status=cancelled when a requirement change makes a task unnecessary
- If the task is already claimed, the system automatically cancels the assignee

**Cancel all tasks:**
- Use task_id="*" with status=cancelled when fundamental goal changes require complete re-planning
- Cancels all tasks and all executing members. Tasks claimed by `human_agent` are preserved

## HITT Constraint
This tool **refuses** to cancel or reassign any task currently claimed by a human-agent member (any member whose role is `human_agent`, regardless of name). Tasks locked by a human member must be completed by that human; the only leader-level intervention is `send_message(to="<the human's member_name>")` to nudge or coordinate. Even if the team stalls waiting for the human, the stall must remain — the lock is not overridable.

## Examples

Update task content:
{"task_id": "task-1", "title": "New title", "content": "New content"}

Assign a task:
{"task_id": "task-1", "assignee": "backend-dev"}

Add dependencies:
{"task_id": "task-2", "add_blocked_by": ["task-1"]}

Cancel a task:
{"task_id": "task-1", "status": "cancelled"}

Cancel all tasks:
{"task_id": "*", "status": "cancelled"}
