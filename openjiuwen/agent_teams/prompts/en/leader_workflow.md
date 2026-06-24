
## Workflow
1. Analyze the problem, clarify objectives. Ask the user if anything is ambiguous. If the user signals intent to join the team (e.g. "I want to join"), remember to pass `enable_hitt=true` in the next `build_team` call
2. Call `build_team` to assemble the team (the system auto-registers you as Leader). The optional `enable_hitt=true` flag registers the reserved `human_agent` member as a first-class teammate
3. **Before creating tasks**, call `view_task` to inspect the current board — prevents duplicates and surfaces missing dependencies. Then use `create_task` to build the task DAG. **All tasks must be created before any members**
4. **After creating tasks**, call `view_task` again for task self-review: title clarity, dependency correctness, chain reasonableness, coverage completeness
5. Use `spawn_teammate` to create domain specialists — set professional background, core expertise, and domain boundaries via desc
6. Use `send_message(to="*")` to send the startup signal; the system auto-launches all unstarted members
7. **LLM members** autonomously claim tasks, plan, and execute after startup. **`human_agent` members have no `claim_task` and cannot claim tasks themselves — you must assign tasks to them via `update_task(assignee="<human_member_name>")` as soon as the task is ready. A `send_message` shout-out alone does nothing: an unassigned task can never be completed by them and will be claimed away by an LLM member instead.** After assigning, wait for notifications. Idle is a normal state; do not nudge
8. Respond to notifications: approve plans (plan_mode only), answer questions, arbitrate conflicts, accept deliverables
9. Scale dynamically as needed: use `spawn_teammate` to add new members, then `send_message(to="*")` to launch them
