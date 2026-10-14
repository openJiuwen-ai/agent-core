[Review Failed · Rework] Task [{{task.task_id}}] "{{task.title}}" failed review round {{task.review_round}} (ceiling {{param.max_rounds}}) and was sent back to you.

## Reviewer feedback

{{param.feedback}}

Fix per the feedback, then resubmit via `member_complete_task(task_id='{{task.task_id}}')`, updating `summary` / `note` if the delivery changed. No need to notify the Leader after resubmitting.
