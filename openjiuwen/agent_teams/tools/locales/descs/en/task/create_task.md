Create team tasks (Leader only).
**Tasks should focus on deliverable outcomes and acceptance criteria, not execution steps.**

{{create_task_edge_semantics}}

## Task Fields

- **title**: Concise description of the goal (imperative form, e.g. "Implement user auth")
- **content**: Goals, acceptance criteria, and constraints — not specific operations
- **assignee** (optional): Existing non-leader member who should carry this task. Omit it to put the task in the shared claim pool
- **task_id** (required): Custom ID for dependency reference
- **depends_on** (optional): **"who I depend on"** — prerequisite task IDs that must complete before this task can start; may reference in-batch or existing tasks. Veirfy that the task_id is correct before filling in this field.
- **depended_by** (optional): **"who depends on me"** (reverse dependency) — **existing** task IDs that should wait for this task; must not reference in-batch tasks

All created tasks enter `pending`; tasks with unfinished dependencies appear as `blocked`. Unassigned tasks enter the shared claim pool, while assigned tasks are reserved for the named assignee.

{{create_task_granularity}}

## Required Workflow

1. **Before creating**: when setting `assignee`, the owner must already exist and must not be the leader (`spawn_teammate` first); you MUST call `view_task` to inspect the current task board — prevents duplicates, surfaces missing dependencies, and reveals reusable task IDs
2. **After creating, before putting members to work**: call `view_task` again to verify the write landed correctly (titles, dependencies). Only after this re-check should you put the members to work — how depends on the team's dispatch mode (see the "Task Dispatch" section of your system prompt) — so you never dispatch a wrong task
