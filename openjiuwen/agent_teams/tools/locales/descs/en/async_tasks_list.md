List every background async task on this harness and its status.

## When to use
- You want to see the launched background tasks (e.g. a swarmflow run) and each one's running/completed/error status;
- You are unsure whether a background task is still in flight.

## Output
One task per line: task_id, tool name, status (running/completed/error), description.

## Notes
- A background task's result is **fed back to you automatically** on completion — do **not** poll this tool to wait for completion.
- Call it only when you want an overview or need to locate a specific task_id.
