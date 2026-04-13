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
- **depends_on** (optional): Prerequisite task IDs that must complete first
- **depended_by** (optional): Existing task IDs that should wait for this task (reverse dependency). Listed tasks are automatically blocked until this task completes

All tasks start as `pending` (or `blocked` if they have unresolved dependencies).

## Tips

- Wrap single tasks in an array — tasks is always an array
- Describe goals, not steps: content should contain goals, acceptance criteria, and constraints
- Single owner: each task should be one independently deliverable outcome
- Use depends_on / depended_by to build the dependency DAG
- Check view_task first to avoid creating duplicate tasks
