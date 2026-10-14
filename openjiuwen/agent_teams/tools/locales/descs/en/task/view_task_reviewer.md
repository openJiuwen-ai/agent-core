View team task information (a fallback while reviewing).

**Constraint**: The review request already carries the full information of the task under review — its content and acceptance criteria, its upstream tasks with their completion notes, the assignee's submission note and your previous-round verdict — matching what `action=get` returns. Do **not** call this tool just to read the task details again.

## When to Use

Only when the information in the review request is contradictory or clearly missing and you cannot complete the review from it:

### action=get (requires task_id)
- Read the task's current content, completion note (`note`) and dependencies

### action=list
- See the summary of every task on the board (no content)

## Output

### get
Full single-task detail: content, note (the owner's completion note), blocked_by (upstream dependencies), blocks (downstream dependencies).

### list
Summary per task: task_id, title, status, assignee, blocked_by.
