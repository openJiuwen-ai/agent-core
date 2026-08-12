[Review Exhausted · Escalated] Task [{{task.task_id}}] "{{task.title}}" has exceeded the maximum review rounds ({{param.max_rounds}}). Automatic rework is stopped; the leader has been notified.

## What you need to do

Send a rework summary to the leader via `send_message(to='leader', ...)`, covering:

1. **What you tried** — what you changed in each round, which feedback you acted on
2. **Why it kept failing** — which verification point you believe is the blocker
3. **Blockers or difficulties** — unclear requirements, technical limitations, missing context, etc.

The leader will review your summary together with the reviewer feedback and decide: continue fixing, reassign, adjust requirements, or cancel the task.
