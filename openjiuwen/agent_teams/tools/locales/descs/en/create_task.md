Create team tasks (Leader only).
**Call only on the task-collaboration branch. Whether to create tasks depends on the form of the final result the user expects, not wording about the handling process.**
**Tasks should focus on deliverable outcomes and acceptance criteria, not execution steps.**

{{create_task_edge_semantics}}

## When Not to Call

- The user ultimately wants the team to form a view, judgment, choice, recommendation, or tradeoff and requests no independently verifiable deliverable or completed action → **do not** `create_task`; use `send_message` to start debate
- Complexity, fact-finding, or decomposability does not by itself establish delivery intent; do not mistake the handling process for a task artifact
- User says **do not create tasks**

## When to Call

- The user explicitly asks for work to be completed and an independently verifiable outcome delivered, or for an action to be carried out. The outcome may contain analysis and judgment, but there must be a separate artifact or completion state for the user to accept
- Task-collaboration path: `view_task` → `create_task` → `view_task` self-check → put members to work

## Task Fields

- **title**: Concise description of the goal (imperative form, e.g. "Implement user auth")
- **content**: Goals, acceptance criteria, and constraints — not specific operations
- **assignee** (optional): Existing non-leader member who should carry this task. Omit it to put the task in the shared claim pool
- **task_id** (optional): Custom ID for dependency reference (auto-generated if omitted)
- **depends_on** (optional): **"who I depend on"** — prerequisite task IDs that must complete before this task can start; may reference in-batch or existing tasks
- **depended_by** (optional): **"who depends on me"** (reverse dependency) — **existing** task IDs that should wait for this task; must not reference in-batch tasks

All created tasks enter `pending`; tasks with unfinished dependencies appear as `blocked`. Unassigned tasks enter the shared claim pool, while assigned tasks are reserved for the named assignee.

{{create_task_granularity}}

## Required Workflow

1. **Before creating**: when setting `assignee`, the owner must already exist and must not be the leader (`spawn_teammate` first); you MUST call `view_task` to inspect the current task board — prevents duplicates, surfaces missing dependencies, and reveals reusable task IDs
2. **After creating, before putting members to work**: call `view_task` again to verify the write landed correctly (titles, dependencies). Only after this re-check should you put the members to work — how depends on the team's dispatch mode (see the "Task Dispatch" section of your system prompt) — so you never dispatch a wrong task
