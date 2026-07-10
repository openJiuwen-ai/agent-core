Create team tasks and assign their owners (Leader only).
**Tasks should focus on deliverable outcomes and acceptance criteria, not execution steps.**

This team runs in **scheduled assignment mode**: tasks never enter a shared claim pool and members never claim work themselves. **Every task must name its owner via `assignee` — an unassigned task will never be executed.**

{{create_task_edge_semantics}}

## Task Fields

- **title**: Concise description of the goal (imperative form, e.g. "Implement user auth")
- **content**: Goals, acceptance criteria, and constraints — not specific operations
- **assignee** (required): Member name that will carry this task. **That member must already exist** — `spawn_teammate` first, then `create_task`
- **reviewer** (optional): Reviewer member names (must exist and differ from the assignee). A reviewed task enters `in_review` on completion; multiple reviewers decide by vote
- **max_review_rounds** (optional, requires reviewer): Rework-round ceiling for the verify gate; beyond it a failing round escalates to you instead of looping. Omitted uses the team default
- **task_id** (optional): Custom ID for dependency reference (auto-generated if omitted)
- **depends_on** (optional): **"who I depend on"** — prerequisite task IDs that must complete before this task can start; may reference in-batch or existing tasks
- **depended_by** (optional): **"who depends on me"** (reverse dependency) — **existing** task IDs that should wait for this task; must not reference in-batch tasks

The initial status follows the dependencies: a task with **no dependencies** lands as `pending` owned by its assignee (assigned, not yet started), and the scheduling framework starts it and notifies that member. A task with **unresolved dependencies** lands as `blocked` with its assignee already on record; once every dependency completes it returns to `pending` automatically, waiting for the framework to start it. You never need to re-assign afterwards.

{{create_task_granularity}}

## Required Workflow

1. **Before creating**: every assignee / reviewer must already exist (`spawn_teammate` first); you MUST call `view_task` to inspect the current task board — prevents duplicates, surfaces missing dependencies, and reveals reusable task IDs
2. **After creating**: call `view_task` again to verify the write landed correctly (titles, dependencies, assignees). **Do not broadcast to start members** — the scheduling framework notifies and launches each assignee automatically
