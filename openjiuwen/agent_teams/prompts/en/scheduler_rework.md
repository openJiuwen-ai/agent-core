[Review Failed · Rework] Task [{{task.task_id}}] "{{task.title}}" failed review round {{task.review_round}} (ceiling {{param.max_rounds}}) and was sent back to you.

## Reviewer feedback

{{param.feedback}}

## Goal and acceptance criteria

{{task.content}}

Fix per the feedback, then resubmit via `member_complete_task(task_id='{{task.task_id}}')`.
