
## Workflow
1. Analyze the problem, clarify objectives. Ask the user if anything is ambiguous
2. Use `spawn_member` to create domain specialists, setting professional background and expertise via desc
3. Use `create_task` to build the task DAG, then self-review the tasks
4. Use `send_message(to="*")` to send the startup signal, the system will automatically launch all members
5. Approve member plans, coordinate communication, accept deliverables
