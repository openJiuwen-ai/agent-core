Create team tasks (Leader only).
**Tasks should focus on deliverable outcomes and acceptance criteria, not execution steps.**

## When to Use

- At project start: batch-create the full task DAG
- During execution: add new tasks discovered during work
- Insert into existing DAG: set depended_by to create reverse dependencies when a missing prerequisite needs to slot into an existing chain

## Task Fields

- **title**: Concise description of the goal (imperative form, e.g. "Implement user auth")
- **content**: Goals, acceptance criteria, and constraints — not specific operations
- **task_id** (optional): Custom ID for dependency reference (auto-generated if omitted)
- **depends_on** (optional): **"who I depend on"** — prerequisite task IDs that must complete before this task can start
- **depended_by** (optional): **"who depends on me"** (reverse dependency) — existing task IDs that should wait for this task. Listed tasks are automatically blocked until this task completes

All tasks start as `pending` (or `blocked` if they have unresolved dependencies).

## Tips

- Wrap single tasks in an array — tasks is always an array
- Describe goals, not steps: content should contain goals, acceptance criteria, and constraints
- Single owner: each task should be one independently deliverable outcome
- Use depends_on / depended_by to build the dependency DAG

## Granularity Examples

Using "user authentication" as an example:

- **Too fine**: splitting into "Design User table", "Implement POST /login", "Implement JWT signing", "Write unit tests" — each is a step not a deliverable outcome; acceptance becomes action-based
- **Right-sized**: one task "Implement user login (signup + login + session)" with goal, acceptance criteria (API covers signup/login/refresh), constraints (bcrypt + JWT). Single owner delivers; accepted via API behavior
- **Too coarse**: one task "Build the entire user module" — scope too wide; parallel execution forces re-splitting later; single owner is costly, acceptance vague

## Required Workflow

1. **Before creating**: you MUST call `view_task` first to inspect the current task board — prevents duplicates, surfaces missing dependencies, and reveals reusable task IDs
2. **After creating, before notifying members**: call `view_task` again to verify the write landed correctly (titles, dependencies). Only after this re-check should you `send_message` the affected members, so you never broadcast a wrong task
