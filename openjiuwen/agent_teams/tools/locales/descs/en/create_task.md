Create team tasks (Leader only).
**Tasks should focus on deliverable outcomes and acceptance criteria, not execution steps.**

## Two Usage Scenarios

1. **Batch-create a task graph/subgraph** (initial DAG at project start, or new tasks discovered during work): pass the whole batch in one call and express edges among them with **depends_on only** — depends_on may reference tasks in the same batch (order-independent, forward references allowed) or tasks already on the board. Do not point depended_by at tasks of the same batch; that is a redundant representation of the same edge and is rejected.
2. **Insert into an existing chain** (a missing prerequisite surfaces): the new task lists the **existing tasks that must wait for it via depended_by**; those tasks automatically become blocked until the new task completes. depended_by may only reference tasks already on the board.

The whole call is **atomic**: either every task is created or none is, with the concrete failure reason (cycle, id collision, missing referenced task, ...).

## Task Fields

- **title**: Concise description of the goal (imperative form, e.g. "Implement user auth")
- **content**: Goals, acceptance criteria, and constraints — not specific operations
- **task_id** (optional): Custom ID for dependency reference (auto-generated if omitted)
- **depends_on** (optional): **"who I depend on"** — prerequisite task IDs that must complete before this task can start; may reference in-batch or existing tasks
- **depended_by** (optional): **"who depends on me"** (reverse dependency) — **existing** task IDs that should wait for this task; must not reference in-batch tasks

All tasks start as `pending` (or `blocked` if they have unresolved dependencies).

## Tips

- Wrap single tasks in an array — tasks is always an array
- Describe goals, not steps: content should contain goals, acceptance criteria, and constraints
- Single owner: each task should be one independently deliverable outcome
- In-batch edges have exactly one spelling: the downstream task depends_on the upstream one; depended_by is reserved for slotting into an existing DAG

## Granularity Examples

Using "user authentication" as an example:

- **Too fine**: splitting into "Design User table", "Implement POST /login", "Implement JWT signing", "Write unit tests" — each is a step not a deliverable outcome; acceptance becomes action-based
- **Right-sized**: one task "Implement user login (signup + login + session)" with goal, acceptance criteria (API covers signup/login/refresh), constraints (bcrypt + JWT). Single owner delivers; accepted via API behavior
- **Too coarse**: one task "Build the entire user module" — scope too wide; parallel execution forces re-splitting later; single owner is costly, acceptance vague

## Required Workflow

1. **Before creating**: you MUST call `view_task` first to inspect the current task board — prevents duplicates, surfaces missing dependencies, and reveals reusable task IDs
2. **After creating, before notifying members**: call `view_task` again to verify the write landed correctly (titles, dependencies). Only after this re-check should you `send_message` the affected members, so you never broadcast a wrong task
