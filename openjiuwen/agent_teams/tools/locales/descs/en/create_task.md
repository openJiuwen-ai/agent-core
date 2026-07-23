Create team tasks (Leader only).
**Tasks should focus on deliverable outcomes and acceptance criteria, not execution steps.**

{{create_task_edge_semantics}}

## Task Fields

- **title**: Concise description of the goal (imperative form, e.g. "Implement user auth")
- **content**: Goals, acceptance criteria, and constraints — not specific operations
- **task_id** (optional): Custom ID for dependency reference (auto-generated if omitted)
- **depends_on** (optional): **"who I depend on"** — prerequisite task IDs that must complete before this task can start; may reference in-batch or existing tasks
- **depended_by** (optional): **"who depends on me"** (reverse dependency) — **existing** task IDs that should wait for this task; must not reference in-batch tasks

All tasks start as `pending` (or `blocked` if they have unresolved dependencies), to be claimed by members themselves.

{{create_task_granularity}}

## Required Workflow

1. **Before creating**: the members who will carry the tasks must already exist (`spawn_teammate` first); you MUST call `view_task` to inspect the current task board — prevents duplicates, surfaces missing dependencies, and reveals reusable task IDs
2. **After creating, before putting members to work**: call `view_task` again to verify the write landed correctly (titles, dependencies). Only after this re-check should you put the members to work — how depends on the team's dispatch mode (see the "Task Dispatch" section of your system prompt) — so you never dispatch a wrong task
