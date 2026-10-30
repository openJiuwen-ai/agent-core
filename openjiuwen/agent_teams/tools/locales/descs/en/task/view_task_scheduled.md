View team task information.

**Constraint**: Task state changes (creation, start, completion, assignment, unblocking, verification verdicts) are pushed to you immediately — do NOT call repeatedly to poll for progress. This team runs in **scheduled assignment mode**: the scheduling framework assigns tasks and notifies you when to start — **do not use this tool to hunt for work**.

## When to Use

### action=list (default, zero-parameter call)
- Check overall progress, identify blocked tasks and bottlenecks
- See what the other members are working on

### action=get (requires task_id)
- Read the content and completion note (`note`) of **another member's** task — e.g. what your downstream needs from you, or what your upstream delivered
- Your own start notice already carries the goal, acceptance criteria and an upstream/downstream brief; you rarely need to get your own task

### action=note (requires task_id)
- Read a task's full completion summary (`summary`) and completion detail (`note`) — the summaries in digests and upstream lists are one truncated line

### action=in_review
- As a reviewer, list the tasks awaiting your verdict

## Output

### list
Summary per task: task_id, title, status, assignee, blocked_by.
Does not include content — use action=get for single-task detail.

### get
Full single-task detail: content, summary / note (the owner's completion summary and detail), blocked_by (upstream dependencies), blocks (downstream dependencies).

### note
A task's completion summary (`summary`) and full completion detail (`note`), untruncated.

## Tips

- list omits content, keeping token cost low. Use get for detail on a specific task.
- Starts, reviews and handoffs are all performed by the scheduling framework; this tool is for **looking**, not for deciding what to do next.
