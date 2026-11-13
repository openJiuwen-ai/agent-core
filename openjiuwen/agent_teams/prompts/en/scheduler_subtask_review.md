[Subtask Review] Your suspended task [{{task.task_id}}] "{{task.title}}" has been edited (now revision r{{task.revision}}), and its subtasks were split off an older revision. Review each subtask against the new content below:

- Needs changing: `update_task(task_id=<subtask>, title=..., content=..., change_note=...)` — the change is pushed to its executor
- Unaffected: `update_task(task_id=<subtask>, confirm=true)` — confirm it still holds
- No longer needed: `update_task(task_id=<subtask>, status="cancelled")` — its own subtasks are cancelled with it

**Do not keep working on [{{task.task_id}}] itself** — end your turn once the review is done; it resumes to you as usual when every subtask is over.

{{task.change_notes}}

## Latest goal and acceptance criteria

{{task.content}}

{{task.revision_diff}}

## Subtasks

{{task.subtask_brief}}
