[Task Assigned] Task [{{task.task_id}}] "{{task.title}}" has been assigned to you.

## Task

{{task.content}}

## How to complete it

You are a passive human member: do the actual work through your external channel, then relay `member_complete_task(task_id='{{task.task_id}}')` to mark it done.
- Relay `view_task` whenever you need the details.
- Relay `send_message` to reach the team for clarification or coordination.
- Reviewers on this task: {{task.reviewer}} (if any).
