Handle **the subtasks of a task you hold**: edit one, confirm it still holds, or cancel it.

Mainly for after the parent has been edited. The subtasks were split off the parent's older revision and are now stale (`view_task` marks them `[stale]`); review each against the parent's new content:

- **Needs changing**: pass `title` / `content` (ideally with a `change_note` on what changed and why). The latest content is pushed to the subtask's executor, who adjusts and carries on; if that subtask has subtasks of its own suspended under it, its executor reviews the next level in turn
- **Unaffected**: pass `confirm=true` to confirm it still holds under the parent's new revision. Its content is untouched and its executor is not disturbed
- **No longer needed**: pass `status="cancelled"`; its own subtasks are cancelled with it

## Limits

- Only subtasks whose parent you currently hold; other members' tasks, top-level tasks and finished tasks cannot be handled
- Subtasks cannot be reassigned, given reviewers or rewired here — ask the Leader
- While reviewing a suspended parent, **do not keep working on the parent itself**; end your turn once the review is done — it resumes to you as usual when every subtask is over
