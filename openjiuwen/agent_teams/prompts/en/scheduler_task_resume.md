[Task Resumed] Every subtask you split off is over (cancelled ones included), and the scheduling framework has started task [{{task.task_id}}] "{{task.title}}" again — carry on and finish it.

## Goal and acceptance criteria

{{task.content}}

## Subtask and upstream results

{{task.upstream_brief}}

Listed above are the completion summaries of your subtasks and of this task's other direct upstream; a subtask without a summary may have been cancelled — check with `view_task(action=get, task_id=...)`.

## How to deliver

- Fold the subtask results into this task's delivery, then call `member_complete_task(task_id='{{task.task_id}}', summary=..., note=...)` — subtask results are not reported to the leader on their own, so your `summary` / `note` is the whole handoff.
- Downstream (waiting on your delivery):
{{task.downstream_brief}}
- Reviewers for this task: {{task.reviewer}}. The framework hands your submission over for review — do not contact the reviewers yourself.
