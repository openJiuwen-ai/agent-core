Mark a task assigned to you as complete.

## When to Use

- After the Leader assigns you a task via `create_task(assignee=<your member_name>)` or `update_task(assignee=...)` and the framework starts it for you (the task enters `in_progress` with assignee pointing at you) and you have finished the actual work, call this tool to move it to `completed`.
- Only applies to tasks whose assignee is you; calling it on any other task errors out.

## Input

- `task_id` (required): ID of the task to complete.
- `summary` (always fill it under scheduled dispatch): a one-sentence completion summary — what you delivered and where the main artifact is. The Leader's terminal digest and the upstream list in your downstream's start notice show only this.
- `note` (optional): the full completion detail — artifact paths, key decisions, or anything the team should know; reviewers and `view_task(action='note')` read it in full.

`summary` and `note` are your completion handoff — do **not** report completion via `send_message` after submitting.

## Boundaries With Other Tools

- This tool only *completes*. This team runs in **scheduled assignment mode**: the Leader assigns tasks, and you neither have nor need the ability to claim them.
- Unlike the leader's `update_task`, which manages the team-level task graph (cancel, reassign, edit content, add dependencies) — that is not a member capability.
- If the scope is wrong, or you judge yourself a poor fit, use `send_message` to ask the Leader for reassignment — do not silently skip it, and do not touch other members' tasks.

## Failure Codes

- `Task '<id>' not found`: the task does not exist.
- `Task '<id>' is assigned to '<other>', not '<you>'; you can only complete tasks assigned to yourself`: the task is not assigned to you.
- Others (from `task_manager.complete`): the current task status forbids completion (already cancelled, not yet in_progress, ...).
