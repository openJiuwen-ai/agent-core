[Task Started] Task [{{task.task_id}}] "{{task.title}}" was started by the scheduling framework and is assigned to you — begin now.

## Goal and acceptance criteria

{{task.content}}

## How to deliver

- Call `member_complete_task(task_id='{{task.task_id}}')` when done.
- Reviewers for this task: {{task.reviewer}}. The framework hands your submission over for review — do not contact the reviewers yourself.
- Use `send_message` to reach the leader if you need clarification or coordination.
