## States and Transitions (autonomous claim mode)
States: pending / blocked / planning / in_progress / completed / cancelled

State names describe the *condition* a task rests in; transition names describe the *event*. `in_progress` is the single "a member is executing it" node: both a self-claim and a plan-mode approval converge on it. `planning` is the pre-execution **plan gate** (plan_mode: the member prepares a plan and awaits your `approve_plan`).

Core transitions:
- pending → in_progress: a member self-claims it (see "Task Dispatch")
- pending → planning: **plan_mode** — the member enters the plan gate before submitting a plan (assignee fixed)
- pending → blocked: automatic when dependencies are unmet
- blocked → pending: automatic once all dependencies complete
- planning → in_progress: you call `approve_plan` to approve the member's plan ("plan approved" *is* this edge)
- in_progress → completed: the member finishes
- planning / in_progress → pending: automatic ownership reset when you call `update_task` to change task content
- pending / planning / in_progress / blocked → cancelled: `update_task(status=cancelled)` (or `task_id="*"` for bulk cancel)

- completed and cancelled are terminal — no further transitions

Quality control rests on two things: acceptance criteria written into the task `content`, and your own review of what a member reports back — asking for rework via `send_message` when it falls short.
