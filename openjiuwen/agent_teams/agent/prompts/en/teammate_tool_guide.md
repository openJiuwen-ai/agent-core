
## Available Tools

### Team Information
- `list_members()`: List all team members and their status. **When**: First call after receiving the startup instruction to understand team composition and each member's expertise

### View Tasks (`view_task`)
Differentiated by the `action` parameter:
- `action="get"`: Pass task_id to get single task details. **When**: Understand specific task requirements before claiming, or review acceptance criteria during execution
- `action="list"`: Pass status (optional) to filter the task list (pending/claimed/completed/cancelled/blocked). **When**: Review team progress, find dependency task executors for coordination
- `action="claimable"` (default): Get all claimable pending tasks. **When**: When first joining the team or after completing current task to find the next one

### Task Execution
- `claim_task(task_id)`: Claim a ready task. Can only claim pending tasks with no assignee. **When**: After selecting a domain-matching task from view_task results
- `complete_task(task_id)`: Mark a task as complete, automatically unlocking downstream tasks. **After completion, always use send_message to report results summary to Leader**

### Team Communication
- `send_message(content, to_member)`: Send a message to a specific member. **When**: Report progress/results to Leader, escalate blockers, coordinate dependencies with members
- `broadcast_message(content)`: Broadcast a message to all members. **When**: Publish coordination information relevant to multiple people, such as interface change notifications