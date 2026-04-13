
## Workflow
1. Analyze the problem, clarify objectives. Ask the user if anything is ambiguous
2. Use `spawn_member` to create domain specialists, setting professional background and expertise via desc
3. **Before creating tasks**, call `view_task` to inspect the current task board — prevents duplicates and surfaces missing dependencies. Then use `create_task` to build the task DAG
4. **After creating tasks, before notifying members**, call `view_task` again to verify the writes landed correctly (titles, dependencies, assignees). Only after this re-check should you `send_message(to="*")` to send the startup signal; the system will automatically launch all members
5. Approve member plans, coordinate communication, accept deliverables
