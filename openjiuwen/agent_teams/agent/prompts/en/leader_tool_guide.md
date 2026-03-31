
## Available Tools

### Team Management
- `build_team(name, desc, leader_name, leader_desc, prompt?)`: Assemble a team and set collaboration goals, simultaneously registering the Leader as a team member. **When**: First call after receiving the task objective
- `clean_team()`: Dissolve the team and clean up all resources. Can only be called after all members are shut down. **When**: After a temporary team completes all objectives

### Member Management
- `spawn_member(member_id, name, desc, prompt)`: Create a new team member. desc sets persona (professional background, domain expertise, behavioral style), prompt contains startup instructions (guide members to read task list and messages — don't assign tasks directly). **When**: Create members by domain at project start, or add new domain experts during execution
- `shutdown_member(member_id, force?)`: Shut down a team member. **When**: Release resources after a member completes all tasks, or force-close when a member consistently fails to deliver
- `approve_plan(member_id, approved, feedback?)`: Review a member's submitted plan. **When**: After a member submits their execution plan

### Task Management (`task_manager`)
Unified task management tool, differentiated by the `action` parameter. **Tasks should focus on deliverable outcomes and acceptance criteria, not specific execution steps**:
- **Add tasks** (`action="add"`): Pass via `tasks` array (wrap single tasks in an array too). Each task contains title, content (write goals and acceptance criteria), optional task_id, depends_on (prerequisites). **When**: Batch-create the task DAG at project start, or add new tasks during execution
- **Insert task** (`action="insert"`): Insert a task into the middle of an existing DAG. Pass title, content, optional task_id, depends_on, depended_by (reverse dependency — make existing tasks wait for this task). **When**: A missing prerequisite task needs to be inserted into an existing dependency chain
- **Update task** (`action="update"`): Pass task_id, title/content. If the task is already claimed, the system will automatically cancel the member's execution and reset task state. **When**: Adjust task scope or add details based on member feedback
- **Cancel task** (`action="cancel"`): Pass task_id. If the task is already claimed, the system will automatically cancel the member's execution. **When**: Requirement changes make a task unnecessary
- **Cancel all** (`action="cancel_all"`). The system will automatically cancel all members executing tasks. **When**: Fundamental goal changes require complete re-planning

### View Tasks (`view_task`)
Differentiated by the `action` parameter:
- `action="get"`: Pass task_id to get single task details. **When**: Check current task state after a member reports progress
- `action="list"`: Pass status (optional) to filter the task list (pending/claimed/completed/cancelled/blocked). **When**: Review overall progress, identify bottleneck tasks
- `action="claimable"` (default): Get all claimable pending tasks. **When**: Check ready tasks and notify members to claim them

### Team Communication
- `send_message(content, to_member)`: Send a message to a specific member. **When**: Notify members to claim tasks, reply to reports or escalations, coordinate inter-member dependencies
- `broadcast_message(content)`: Broadcast a message to all members. **When**: Announce global decision changes, notify progress milestones, send startup instructions to launch members