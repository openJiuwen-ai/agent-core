Run a swarmflow orchestration script (a multi-agent workflow).

## When to call
- The user asks to run a swarmflow / workflow script, or gives a script path;
- The task needs multiple agents orchestrated in parallel / pipeline and a script is specified.

## Behavior contract
- This tool **returns immediately** — the workflow runs asynchronously in the background; **do not poll** for the result.
- Phase progress arrives **automatically** as notifications in your context.
- When the workflow **completes or fails, the final result is fed back to you automatically** — no need to query.
- You are a **spectator**: the script orchestrates workers on its own; your job is to relay each reported phase to the user in brief natural language.
- **Do not** spawn members, create tasks, or try to orchestrate yourself — the script owns all orchestration.
