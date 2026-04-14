View team task information.

## When to Use

### action=list (default, zero-parameter call)
- Check overall progress on all tasks
- Identify blocked tasks and bottlenecks
- After completing a task, check for newly unblocked work
- Before assigning tasks, review current task states

### action=get (requires task_id)
- Get full requirements and acceptance criteria before starting work
- Understand task dependencies (blocked_by: what blocks it, blocks: what it blocks)
- Verify task state after a member reports progress

### action=claimable
- Quickly get all pending tasks ready to claim
- After completing a task, find the next available one

## Output

### list / claimable
Summary per task: task_id, title, status, assignee, blocked_by.
Does not include content — use action=get for single-task detail.

### get
Full single-task detail: includes content, blocked_by (upstream dependencies), blocks (downstream dependencies).

## Tips

- list omits content, keeping token cost low. Use get for detail on a specific task.
- Tasks with non-empty blocked_by cannot be claimed — complete their prerequisites first.
- Prefer tasks in ID order (lowest ID first) when multiple tasks are available, as earlier tasks often set up context for later ones.

## Teammate Workflow

1. After completing your current task, call view_task (default list) to find available work
2. Look for tasks with status=pending, no assignee, and empty blocked_by
3. Claim with claim_task(status=claimed), then use action=get to get full requirements
4. If blocked, focus on unblocking or notify the leader
