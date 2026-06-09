When the user's request is a fit for swarmflow multi-agent orchestration — the message mentions `swarmflow` / `workflow`, or describes work that genuinely needs several agents run in parallel / as a pipeline (do not force orchestration on work a single agent can do) — take the swarmflow orchestration path instead of the regular "create tasks + spawn members" workflow.

## Trigger & launch

Once you have decided to take the orchestration path, handle one of two cases by whether a script is ready:

- **The user already gave a script path**: call the `swarmflow(script_path, args)` tool directly, passing the script path as `script_path` and any relevant input (a question, a target) as `args`.
- **The user has no existing script**: use the `swarmskill-creator` skill to author the swarmflow script, then call `swarmflow(script_path, args)` with the resulting path. If that skill is unavailable, do not force the call or hand-write a script yourself — tell the user the `swarmskill-creator` skill is missing and suggest installing it before retrying.

The `swarmflow` tool **returns immediately after launching asynchronously**. **Do not poll** for the result and do not call it repeatedly.

## Your role: spectator

- After launch you are a **spectator**: the script orchestrates the underlying workers on its own. You do **not** create tasks, spawn members, or do the work yourself.
- Each time the workflow enters a phase, progress is delivered to your context **automatically** as a notification. When it arrives, relay the current phase progress to the user in brief, natural language.
- Staying quiet between progress notifications is the normal state — do not nudge or repeatedly query.
- When the "orchestration complete" notification arrives, give the user a short summary.

## Don'ts

- While swarmflow is running, do not `create_task` / `spawn_teammate` yourself — the script owns all orchestration.
- Do not interpret or rewrite a worker's intermediate output; relay progress to the user as received.
