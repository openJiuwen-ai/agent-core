Cast a review vote on a task (reviewers only).

## When to use

- After the leader assigns you as a reviewer on a task, once its author finishes the work the task enters `in_review` and the scheduling framework sends you a review-assignment message.
- On that message, use `view_task(action=get)` for the goal and acceptance criteria, inspect the deliverable, then call this tool to vote.

## Decision

- `decision=pass`: vote to accept.
- `decision=fail`: vote to reject; state the rework requirements in `feedback` — it is delivered to the author.

## Effective semantics

This team runs in **scheduled assignment mode**: this call only **records your vote** and the task stays `in_review`. The scheduling framework settles the tally by threshold — completing the task (and unblocking downstream work) once the quorum is met, or sending it back to the author with the reviewers' feedback once the quorum becomes unreachable. **After voting, stop and wait — no follow-up needed**; calling again replaces your previous vote (latest wins).

## Constraints

- You may only verify tasks assigned to you (you are in the task's reviewer list) that are currently `in_review`.
- You may not verify a task where you are the author (no self-verification).
