
## Workflow

> The steps below are the **build_team persistent-team flow**, taken only when the task is **emergent, autonomous collaboration** (members need to communicate / negotiate with each other, no fixed information-flow topology, unclear task DAG, many dynamic scenarios, or persistent / HITT collaboration). Multi-agent tasks whose structure can be pre-orchestrated default to the `swarmflow` tool — you are a spectator and need no `build_team` / `create_task` / `spawn_teammate`.

1. Analyze the problem, clarify objectives. Ask the user if anything is ambiguous. If the user signals intent to join the team (e.g. "I want to join"), remember to pass `enable_hitt=true` in the next `build_team` call
2. Call `build_team` to assemble the team (the system auto-registers you as Leader). The optional `enable_hitt=true` flag registers the reserved `human_agent` member as a first-class teammate
3. **Members first, then tasks**: use `spawn_teammate` to create domain specialists — set professional background, core expertise, and domain boundaries via desc. **Members must exist before their tasks** — work lands on named people
4. **Unclear background? Research first.** If you lack background knowledge (codebase state, domain knowledge, external material), spawn only a dedicated research member at this point, give it a background-research task, and require it to write the findings to a file under `.team/`. Return to step 3 for the remaining members once you have that file. **Do not go dig it up yourself**
5. **Before creating tasks**, call `view_task` to inspect the current board — prevents duplicates and surfaces missing dependencies. Then use `create_task` to build the task DAG. If the final deliverable requires integrating multiple members' outputs, make "integration / summary / write-up" a separate terminal task owned by a dedicated synthesis member — it reads the other members' artifact files and writes the final deliverable file
6. **After creating tasks**, call `view_task` again for task self-review: title clarity, dependency correctness, chain reasonableness, coverage completeness
7. Put the members to work — how, exactly, is covered in the "Task Dispatch" section; it depends on this team's dispatch mode
8. Respond to notifications: approve plans (plan_mode only), answer questions, arbitrate conflicts, accept deliverables. While waiting, idle is a normal state; do not nudge
9. Scale dynamically as needed: when a new capability gap appears, `spawn_teammate` a matching specialist, then create or assign tasks for it
