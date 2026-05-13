Mark a task that is assigned to the calling member as completed.

## When to use

- After the leader assigns a task to you via `update_task(assignee=<your_member_name>)` (the task moves to status `claimed` with you as assignee), and you have finished the actual work, call this tool to move the status to `completed`.
- Only applicable when `task.assignee == you`. Other tasks will be rejected with a clear error.

## Input

- `task_id` (required): ID of the task to complete.
- `note` (optional): Completion note describing the deliverable path, key decisions, or anything the team should be aware of.

## Boundaries vs. other tools

- This is not a *claim* tool. It only completes; you should not autonomously claim tasks. Tasks reach you because the leader assigned them; do not try to grab work the leader did not give you.
- Distinct from leader's `update_task`: `update_task` manages the team-level task graph (cancel / reassign / edit content / add deps) and is leader-only.
- Distinct from teammate's `claim_task`: `claim_task` couples claim and complete in one call. This tool only completes.

## Failure modes

- `Task '<id>' not found`: the id does not exist.
- `Task '<id>' is assigned to '<other>', not '<you>'; you can only complete tasks assigned to yourself`: the task is not yours.
- Other (from `task_manager.complete`): the task is in a status that cannot transition to completed (e.g. already cancelled, never claimed).
