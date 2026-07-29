Cancel a background async task that is still running.

## When to use
- A background task (e.g. a swarmflow run) has gone astray or is no longer needed and you want to stop it.

## Parameters
- task_id: the id of the task to cancel (look it up with async_tasks_list first).

## Notes
- After cancellation the task's status becomes error (cancelled) and no result will be fed back.
