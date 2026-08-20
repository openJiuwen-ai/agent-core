## States and Transitions (scheduled assignment mode)
States: pending / blocked / planning / in_progress / in_review / completed / cancelled

State names describe the *condition* a task rests in; transition names describe the *event*. `in_progress` is the single "a member is executing it" node: both a scheduled framework start and a plan-mode approval converge on it. `planning` is the pre-execution **plan gate** (plan_mode: the member prepares a plan and awaits your `approve_plan`). `in_review` is the post-execution **verify gate**: when a task has `reviewer`s, the member's completion enters it to await a verdict.

Core transitions:
- pending → in_progress: the framework starts an already-assigned task (the assignee was fixed at create time; this only begins execution)
- pending → planning: **plan_mode** — the member enters the plan gate before submitting a plan (assignee fixed)
- pending → blocked: automatic when dependencies are unmet
- blocked → pending: automatic once all dependencies complete
- planning → in_progress: you call `approve_plan` to approve the member's plan ("plan approved" *is* this edge)
- in_progress → in_review: the member completes and the task has `reviewer`s — it enters the verify gate for a verdict
- in_progress → completed: the member completes and the task has no `reviewer` — it finishes directly
- in_review → completed: verification passed; the framework flips it once the votes are in
- in_review → in_progress: verification failed; the framework sends it back and the author reworks against the feedback
- planning / in_progress / in_review → pending: automatic ownership reset when you call `update_task` to change task content
- pending / planning / in_progress / in_review / blocked → cancelled: `update_task(status=cancelled)` (or `task_id="*"` for bulk cancel)

- completed and cancelled are terminal — no further transitions

**Verify gate (reviewers)**: when a task's result needs verification, assign one or more **reviewers** with `create_task(reviewer=[...])` or `update_task(reviewer=[...])` (they must not be the assignee). A task with reviewers does not complete directly — after the author finishes it enters `in_review`, and the scheduling framework summons the reviewers, collects their verdicts and flips the state on its own. **You neither need to nor should chase verification manually.** For reviewer types, counts and how to write their instructions, see "Task Dispatch". Tasks that need no verification simply carry no reviewer and behave as before.
