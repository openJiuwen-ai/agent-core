View team task information (a fallback while reviewing).

**Constraint**: The review request already carries the full information of the task under review — its content and acceptance criteria, its upstream tasks with their completion summaries, the assignee's submission (summary and full note) and your previous-round verdict. Read an upstream task's full completion detail with `action=note` when you need it. Do **not** call this tool just to read the task details again.

## When to Use

Only when the information in the review request is contradictory or clearly missing and you cannot complete the review from it:

### action=get (requires task_id)
- Read the task's current content, completion note (`note`) and dependencies

### action=note (requires task_id)
- Read a task's full completion summary (`summary`) and completion detail (`note`) — the summaries in digests and upstream lists are one truncated line

### action=list
- See the summary of every task on the board (no content)

## Output

### get
Full single-task detail: content, summary / note (the owner's completion summary and detail), blocked_by (upstream dependencies), blocks (downstream dependencies).

### note
A task's completion summary (`summary`) and full completion detail (`note`), untruncated.

### list
Summary per task: task_id, title, status, assignee, blocked_by.
