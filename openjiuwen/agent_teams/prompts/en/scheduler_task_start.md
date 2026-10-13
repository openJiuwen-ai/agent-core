[Task Started] Task [{{task.task_id}}] "{{task.title}}" was started by the scheduling framework and is assigned to you — begin now.

## Goal and acceptance criteria

{{task.content}}

## Upstream and downstream

- Upstream (completed, yours to build on):
{{task.upstream_brief}}
- Downstream (waiting on your delivery):
{{task.downstream_brief}}

## How to deliver

- Call `member_complete_task(task_id='{{task.task_id}}', note=...)` when done; put artifact paths and key decisions in `note` — it is handed to your downstream with their start notice.
- Reviewers for this task: {{task.reviewer}}. The framework hands your submission over for review — do not contact the reviewers yourself.
- Use `send_message` to reach the leader if you need clarification or coordination.
