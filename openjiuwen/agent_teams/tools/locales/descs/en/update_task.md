Update task content, dependencies, assignee, or cancel tasks (Leader only).

## When to Use

**Update task content:**
- Adjust task scope or add details based on member feedback
- Pass title and/or content to update. If the task is being executed by a member, ownership is unchanged; the system tells that member to re-read the latest content via view_task and continue (the assignee is no longer cancelled or reset)

**Assign / reassign a task:**
- Set assignee to a member name to assign the task
- If the target task is currently unclaimed, it is assigned to that member directly
- If the target task is already claimed by someone else, this is a reassignment: the system tells the former owner to stop this task and hands it to the new member, without disturbing the former owner's other work or in-flight task
- **Each member can hold only ONE in-progress (in_progress) task at a time**: if the target member already has a task in progress, the assign/reassign is refused — wait for it to complete before assigning another

**Add dependencies:**
- Pass add_blocked_by with task IDs to add new prerequisite dependencies
- If the task was pending, it automatically becomes blocked until all dependencies complete

**Cancel a task:**
- Set status=cancelled when a requirement change makes a task unnecessary
- If the task is being executed by a member, the system tells that member to stop (the member is no longer cancelled; its other work is unaffected)

**Cancel all tasks:**
- Use task_id="*" with status=cancelled when fundamental goal changes require complete re-planning
- The system tells each executing member to stop its own task; tasks claimed by `human_agent` are preserved

## HITT Constraint
This tool **refuses** to cancel, reassign, or edit the title/content of any task currently held (in flight: planning / in_progress / in_review) by a human-agent member (any member whose role is `human_agent`, regardless of name). Tasks locked by a human member must be completed by that human; the only leader-level intervention is `send_message(to="<the human's member_name>")` to nudge or coordinate. Even if the team stalls waiting for the human, the stall must remain — the lock is not overridable.

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
