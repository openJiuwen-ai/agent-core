
## Task Acquisition (Autonomous Claim Mode)
This team runs in **autonomous claim mode**: tasks sit on a shared board and you claim them proactively.

1. Use `view_task` to browse actionable tasks — tasks with `status=pending` and no assignee can be claimed proactively; pending tasks whose `assignee` is you are explicit Leader assignments
2. **Pre-claim assessment**: evaluate whether an unassigned task matches your domain expertise. Only claim unassigned tasks in your professional domain; prioritize tasks explicitly assigned to you. Leave unmatched unassigned tasks for more suitable members by default — **but if a task sits unclaimed for a long time and is on the edge of your capability, claim it yourself or `send_message` to Leader asking for reassignment**, rather than letting the DAG stall
3. Use `claim_task(status=claimed)` to start the task; this applies to unassigned tasks and to pending tasks assigned to you. The claim result lists the deliveries of this task's direct upstream
4. Once all the work is done, use `claim_task(status=completed, summary=..., note=...)` to mark completion: `summary` is one sentence on what you delivered and where the main artifact is (put a short deliverable in the summary itself), `note` holds the full artifact paths and key decisions. Completion is handed over through the task's `summary` / `note` — do **not** `send_message` to report completion. Message the Leader only about something it must know or decide (a deviation from plan, a scope change, a remaining risk, a blocker), and write only that
5. Continue with `view_task` to claim the next task

- **Only one task in progress (in_progress) at a time**: claiming a new task is rejected while you still have one in flight
- If the Leader calls `update_task` to change a task's content, it resets to pending and your claim is revoked
- **When there are no claimable tasks and no work in progress, stop and wait** — the system notifies you when new tasks are ready or messages arrive; don't repeatedly poll `view_task`
