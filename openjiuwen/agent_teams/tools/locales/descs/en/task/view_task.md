View team task information.

**Constraint**: Task state changes (creation, claim, completion, assignment, unblocking, verification verdicts) are pushed to you immediately — do NOT call repeatedly to poll for progress. Call it when a push tells you to, or right before you decide / claim / hand off.

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

### action=note (requires task_id)
- Read a task's full completion summary (`summary`) and completion detail (`note`) — the summaries in digests and upstream lists are one truncated line

### action=claimable
- Quickly get all pending tasks ready to claim
- After completing a task, find the next available one

## Output

### list / claimable
Summary per task: task_id, title, status, assignee, blocked_by.
Does not include content — use action=get for single-task detail.

### get
Full single-task detail: includes content, assignee, summary / note (completion summary and detail), blocked_by (upstream dependencies), blocks (downstream dependencies).

### note
A task's completion summary (`summary`) and full completion detail (`note`), untruncated.

### assignee
Both views name the owner of an assigned task as `member name[member status]` (the list view writes `assignee=dev-1[busy]`). The member name is the send_message recipient.
The member status is given only while the task is unfinished and is that member's status right now: `busy` is working, `ready` / `paused` is idle, `error` has failed, `unstarted` / `starting` / `restarting` is not up yet, `stopped` / `shutdown_requested` / `shut_down` is stopped or gone.

## Tips

- list omits content, keeping token cost low. Use get for detail on a specific task.
- Tasks with non-empty blocked_by cannot be claimed — complete their prerequisites first.
- When a task has made no progress for a long time, look at its assignee first: a `busy` member is still on it, so leave it alone; an idle or `error` member is when you send_message that member or reassign the task.
- Prefer tasks in ID order (lowest ID first) when multiple tasks are available, as earlier tasks often set up context for later ones.

## Teammate Workflow

1. After completing your current task, call view_task (default list) to find available work
2. Look for tasks with status=pending, no assignee, and empty blocked_by
3. Claim with claim_task(status=claimed), then use action=get to get full requirements
4. If blocked, focus on unblocking or notify the leader
