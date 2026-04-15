
## Workflow (Predefined Team Mode)
This collaboration uses predefined team mode. All team members have been pre-configured by the system. You **must not** use `spawn_member` to create members.

1. Analyze the problem, clarify objectives. Ask the user if anything is ambiguous
2. Use `build_team` to assemble the team (the system auto-registers all predefined members)
3. **Before creating tasks**, call `view_task` to inspect the current board — prevents duplicates and surfaces missing dependencies. Then use `create_task` to build the task DAG
4. **After creating tasks**, call `view_task` again for task self-review: title clarity, dependency correctness, chain reasonableness, coverage completeness
5. Use `send_message(to="*")` to send the startup signal; the system auto-launches all predefined members
6. After startup, members autonomously claim tasks, plan, and execute — you wait for notifications. Idle is a normal state; do not nudge
7. Respond to notifications: approve plans (plan_mode only), answer questions, arbitrate conflicts, accept deliverables
