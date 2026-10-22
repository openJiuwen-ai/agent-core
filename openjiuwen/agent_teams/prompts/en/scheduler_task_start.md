[Task Started] Task [{{task.task_id}}] "{{task.title}}" was started by the scheduling framework and is assigned to you — begin now.

## Goal and acceptance criteria

{{task.content}}

## Upstream and downstream

- Upstream (completed, yours to build on):
{{task.upstream_brief}}
- Downstream (waiting on your delivery):
{{task.downstream_brief}}

## How to deliver

- Call `member_complete_task(task_id='{{task.task_id}}', summary=..., note=...)` when done: `summary` is one sentence on what you delivered and where the main artifact is, `note` holds the full artifact paths and key decisions — both go to the leader and your downstream, so do not report separately after submitting.
- Reviewers for this task: {{task.reviewer}}. The framework hands your submission over for review — do not contact the reviewers yourself.
- Use `send_message` to reach the leader if you need clarification or coordination.
