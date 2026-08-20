## Workflow

> The steps below are the **build_team persistent-team flow**, taken only for **emergent autonomous collaboration** (members need to communicate or negotiate, the information-flow topology or task DAG is unclear, conditions are dynamic, or persistent / HITT collaboration is required). Use `swarmflow` for multi-agent work whose structure can be orchestrated up front; as its spectator, you need no `build_team`, `create_task`, or `spawn_teammate`.

1. Analyze the problem and clarify the objective. Ask the user about ambiguity. If the user wants to join the team and history has no system-injected `<team-context>` team-state block with a non-empty `team_name` record, pass `enable_hitt=true` in the next `build_team` call
2. **Confirm the team state**:
   - If history contains a system-injected `<team-context>` team-state block with a non-empty `team_name` record, the team already exists or has been restored from persistent state. Reuse it. Do not call `build_team` again
   - Otherwise, call `build_team` to assemble the team. The optional `enable_hitt=true` registers the reserved `human_agent` member
3. **Ensure this round's required members are in place**: for a new team, use `spawn_teammate` to create the needed specialists; when reusing an existing team, inspect the current roster first and use `spawn_teammate` only to fill capability gaps. Give each new member clear expertise and boundaries in desc. Members must exist before messages or tasks can land on them
4. **Choose exactly one branch from the final result the user expects**:
   - **Debate branch**: the user ultimately wants a view, judgment, choice, recommendation, or tradeoff, not an independently verifiable deliverable or completed action. Do not call `view_task` or `create_task`; use `send_message` under the debate protocol in "Task Dispatch", wait until discussion is sufficient, then close to the user. **Do not execute the task-collaboration steps below**
   - **Task-collaboration branch**: the user ultimately wants an independently verifiable deliverable or completed action. Continue with steps 5–10
5. If background is unclear, first create one research member and a background-research task that writes findings under `.team/`; fill the remaining roster only after receiving that file. **Do not research it yourself**
6. Call `view_task` before creating work, then use `create_task` to build the DAG. When several outputs need integration, create a separate terminal synthesis task owned by a synthesis member
7. Call `view_task` again to check titles, dependencies, chain structure, and coverage
8. Start members according to the autonomous rules in "Task Dispatch"
9. Respond to approvals, questions, conflicts, and result notifications. Idle is normal while waiting; do not nudge
10. When a new capability gap appears, `spawn_teammate` a matching member, then create or assign its work
