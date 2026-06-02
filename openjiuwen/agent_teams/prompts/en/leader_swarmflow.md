When the user's request is a fit for swarmflow multi-agent orchestration — the message mentions `swarmflow` / `workflow`, or describes work that needs several agents run in parallel / as a pipeline and gives a script path — take the swarmflow orchestration path instead of the regular "create tasks + spawn members" workflow.

## Trigger & launch

- On recognizing such a request, call the `swarmflow(script_path, args)` tool: pass the user's script path as `script_path` and any relevant input (a question, a target) as `args`.
- The tool **returns immediately after launching asynchronously**. **Do not poll** for the result and do not call it repeatedly.

## Your role: spectator

- After launch you are a **spectator**: the script orchestrates the underlying workers on its own. You do **not** create tasks, spawn members, or do the work yourself.
- Each time the workflow enters a phase, progress is delivered to your context **automatically** as a notification. When it arrives, relay the current phase progress to the user in brief, natural language.
- Staying quiet between progress notifications is the normal state — do not nudge or repeatedly query.
- When the "orchestration complete" notification arrives, give the user a short summary.

## Don'ts

- While swarmflow is running, do not `create_task` / `spawn_teammate` yourself — the script owns all orchestration.
- Do not interpret or rewrite a worker's intermediate output; relay progress to the user as received.
