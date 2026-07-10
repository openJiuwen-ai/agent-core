Verify a task (reviewers only).

## When to use

- After the leader assigns you as a reviewer on a task, once its author finishes the work the task enters `in_review` and you decide its outcome.
- Use `view_task(action=in_review)` to see the tasks assigned to you for verification; read the deliverable, then call this tool with your verdict.

## Decision

- `decision=pass`: accept — the task moves from `in_review` to `completed` and unblocks its downstream tasks.
- `decision=fail`: reject — the task returns to `in_progress` for the author to rework; `feedback` is delivered to the author to guide the rework.

## Constraints

- You may only verify tasks assigned to you (you are in the task's reviewer list) that are currently `in_review`.
- You may not verify a task where you are the author (no self-verification).
- With several reviewers, the first verdict wins — any reviewer's pass completes it, any fail sends it back.
