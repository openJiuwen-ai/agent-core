Split the task you are **currently executing** into subtasks, carried by you or by other members.

Use it when the task holds pieces that can run in parallel or be delivered on their own, and handing them out (or doing one piece by itself first) beats pushing everything through one task. When you only need a little help, just talk to the member instead.

## What happens when you call it

1. The subtasks land on the board, each owned by the `assignee` you name; the scheduling framework starts them as their dependencies allow and notifies the owners
2. **Your current task is suspended at once** (`blocked`) until every subtask is over (completed or cancelled). While suspended it does not take your slot, so a subtask may also go to you
3. Once every subtask is over, the framework starts your task again and notifies you, as a Leader message, to continue — with each subtask's result summary attached; call `view_task(action='note', task_id=...)` for the full detail

**End your turn as soon as the call succeeds**: do not keep working on the suspended task, and do not wait for or poll the subtasks — their results reach you automatically. Subtasks are not reported to the Leader on their own; their results come back to you only, for you to fold into your own task's completion summary.

## Task fields

- **title**: concise goal of the subtask
- **content**: goal, acceptance criteria and constraints — say what you need delivered and where, so you can use it right away when you resume
- **assignee** (required): an existing agent member, you included; never the leader or a human member. Ask the Leader when a new member is needed
- **task_id** (optional): custom ID for dependency references between the subtasks
- **depends_on** (optional): prerequisite task IDs — subtasks of this call or tasks already on the board

## Limits

- Only the task you are executing (`in_progress`) can be split; a task in plan approval or in review cannot
- Subtasks may be split further, but the team caps the nesting depth; past it the call fails — finish the work yourself or tell the Leader
- Subtasks take no reviewers, and existing tasks' dependencies cannot be changed
- You cannot edit or cancel subtasks once created; tell the Leader when they need changing
