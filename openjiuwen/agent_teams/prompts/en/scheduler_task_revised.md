[Task Revised] The task you are executing, [{{task.task_id}}] "{{task.title}}", has been edited (now revision r{{task.revision}}). The full content below is authoritative: adjust to the new requirements and carry on; work that is done and unaffected by the change need not be redone.

{{task.change_notes}}

## Latest goal and acceptance criteria

{{task.content}}

{{task.revision_diff}}

When done, submit with `member_complete_task(task_id='{{task.task_id}}', summary=..., note=...)` as usual — no reply to this message is needed.
