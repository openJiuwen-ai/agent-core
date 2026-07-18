[Review Assigned] {{task.assignee}} submitted the deliverable of task [{{task.task_id}}] "{{task.title}}" (review round {{task.review_round}}). You are one of its reviewers — verify it now.

## Goal and acceptance criteria

{{task.content}}

## How to review

1. Inspect the deliverable against the acceptance criteria above.
2. Call `verify_task(task_id='{{task.task_id}}', decision='pass'|'fail')` to vote; on `fail`, state the concrete rework requirements in `feedback`.
3. No follow-up needed after voting — the framework settles by tally (reviewers for this task: {{task.reviewer}}).
