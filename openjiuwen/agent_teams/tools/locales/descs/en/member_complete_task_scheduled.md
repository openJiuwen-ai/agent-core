Mark a task assigned to you as complete.

## When to Use

- After the Leader assigns you a task via `create_task(assignee=<your member_name>)` or `update_task(assignee=...)` and the framework starts it for you (the task enters `in_progress` with assignee pointing at you) and you have finished the actual work, call this tool to move it to `completed`.
- Only applies to tasks whose assignee is you; calling it on any other task errors out.

## Input

- `task_id` (required): ID of the task to complete.
- `note` (optional): Completion note — artifact paths, key decisions, or anything the team should know.

## Boundaries With Other Tools

- This tool only *completes*. This team runs in **scheduled assignment mode**: the Leader assigns tasks, and you neither have nor need the ability to claim them.
- Unlike the leader's `update_task`, which manages the team-level task graph (cancel, reassign, edit content, add dependencies) — that is not a member capability.
- If the scope is wrong, or you judge yourself a poor fit, use `send_message` to ask the Leader for reassignment — do not silently skip it, and do not touch other members' tasks.

## Failure Codes

- `Task '<id>' not found`: the task does not exist.
- `Task '<id>' is assigned to '<other>', not '<you>'; you can only complete tasks assigned to yourself`: the task is not assigned to you.
- Others (from `task_manager.complete`): the current task status forbids completion (already cancelled, not yet in_progress, ...).
