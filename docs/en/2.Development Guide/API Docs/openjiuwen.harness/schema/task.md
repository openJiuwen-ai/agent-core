# openjiuwen.harness.schema.task

## enum openjiuwen.harness.schema.TaskStatus

```python
class TaskStatus(str, Enum)
```

Lifecycle status of a task in a task plan.

| Value | Description |
|---|---|
| `PENDING` | Task has not started. |
| `IN_PROGRESS` | Task is currently being executed. |
| `COMPLETED` | Task finished successfully. |
| `FAILED` | Task encountered an error. |

---

## class openjiuwen.harness.schema.TaskItem

A single task within a [`TaskPlan`](#class-openjiuwenharnessschematasktaskplan).

**Attributes**:

- **id** (str): Unique task identifier.
- **title** (str): Short human-readable title.
- **description** (str): Detailed description of the task.
- **status** ([TaskStatus](#enum-openjiuwenharnessschematasktaskstatus)): Current lifecycle status. Default: `PENDING`.
- **depends_on** (list[str]): List of task IDs that must complete before this task. Default: `[]`.
- **result_summary** (str, optional): Brief summary of the result once completed. Default: `None`.

---

## class openjiuwen.harness.schema.TaskPlan

An ordered plan of tasks that the agent works through during the task loop.

**Attributes**:

- **goal** (str): The high-level goal the plan is working toward.
- **tasks** (list[[TaskItem](#class-openjiuwenharnessschematasktaskitem)]): The ordered list of tasks.
- **current_task_id** (str, optional): ID of the task currently in progress. Default: `None`.

### method get_task

```python
get_task(task_id: str) -> TaskItem | None
```

Look up a task by ID.

**Parameters**:

- **task_id** (str): The task ID to find.

**Returns**:

**[TaskItem](#class-openjiuwenharnessschematasktaskitem) | None**: The matching task, or `None`.

### method get_next_task

```python
get_next_task() -> TaskItem | None
```

Return the next `PENDING` task whose dependencies are all `COMPLETED`.

**Returns**:

**[TaskItem](#class-openjiuwenharnessschematasktaskitem) | None**: The next eligible task, or `None` if all tasks are done or blocked.

### method add_task

```python
add_task(task: TaskItem) -> None
```

Append a new task to the plan.

**Parameters**:

- **task** ([TaskItem](#class-openjiuwenharnessschematasktaskitem)): The task to add.

### method mark_in_progress

```python
mark_in_progress(task_id: str) -> None
```

Set a task's status to `IN_PROGRESS` and update `current_task_id`.

**Parameters**:

- **task_id** (str): The task ID.

### method mark_completed

```python
mark_completed(task_id: str, result_summary: str | None = None) -> None
```

Set a task's status to `COMPLETED` and optionally record a result summary.

**Parameters**:

- **task_id** (str): The task ID.
- **result_summary** (str, optional): Summary of the result. Default: `None`.

### method mark_failed

```python
mark_failed(task_id: str, result_summary: str | None = None) -> None
```

Set a task's status to `FAILED` and optionally record a result summary.

**Parameters**:

- **task_id** (str): The task ID.
- **result_summary** (str, optional): Summary of the failure. Default: `None`.

### method get_progress_summary

```python
get_progress_summary() -> str
```

Return a human-readable progress summary (e.g. `"3/5 tasks completed"`).

**Returns**:

**str**: Progress summary string.

### method to_markdown

```python
to_markdown() -> str
```

Render the task plan as a Markdown checklist.

**Returns**:

**str**: Markdown-formatted task plan.

### method to_dict

```python
to_dict() -> dict
```

Serialize the task plan to a JSON-serializable dictionary.

**Returns**:

**dict**: The serialized plan.

### classmethod from_dict

```python
@classmethod
from_dict(data: dict) -> TaskPlan
```

Reconstruct a `TaskPlan` from a dictionary produced by `to_dict()`.

**Parameters**:

- **data** (dict): The serialized plan dictionary.

**Returns**:

**[TaskPlan](#class-openjiuwenharnessschematasktaskplan)**: The reconstructed plan.
