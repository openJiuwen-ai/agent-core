Retrieve the full output (or error) of a background async task.

## When to use
- When a task's full result was large and spilled to disk, the injected message is a summary plus a path — use this tool to fetch the full text by task_id;
- You want to actively grab a finished task's result instead of waiting for the automatic feedback.

## Parameters
- task_id: the target task id.
- block: whether to block until the task is terminal (default false, returns the current status immediately).
- timeout: maximum wait in milliseconds when block=true (default 30000, capped at 600000).

## Notes
- Results are **fed back automatically** on completion — you usually do not need to call this tool.
- block=true **ties up your current round** until the task is terminal or times out — use it only when you truly need the result synchronously, never to poll.
