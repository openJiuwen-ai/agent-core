# openjiuwen.auto_harness.contexts

Execution context module, providing the runtime environment for Auto Harness pipelines and stages. Includes task runtime dependency encapsulation, layered execution context base classes, and session-level and task-level context implementations.

Submodules:
- `execution`: Runtime context definitions

---

## openjiuwen.auto_harness.contexts.execution.task_key

```python
def task_key(task: OptimizationTask) -> str
```

Return the artifact scope key for a task. Uses the task's `topic` field, falling back to `"task"` if empty.

**Parameters**:
* **task**(`OptimizationTask`): The optimization task object.

**Returns**: A string key for the task scope.

---

## class openjiuwen.auto_harness.contexts.execution.TaskRuntime

```python
@dataclass
class TaskRuntime:
    """Prepared task-scoped execution dependencies."""
```

Pre-prepared task-scoped execution dependency set. Contains related experiences, worktree path, safety rails, pre-existing dirty file list, and stage agent instances required for task execution.

**Fields**:
* **related**(`list[Experience]`): List of experience records related to the current task.
* **wt_path**(`str`): Worktree path.
* **edit_safety_rail**(`Any`): Edit safety rail instance.
* **preexisting_dirty_files**(`list[str]`): List of dirty files existing before the task starts.
* **task_agent**(`Any`): Task implementation agent instance.
* **commit_agent**(`Any`): Commit stage agent instance.
* **task_session**(`Any`): Task session instance, default `None`.
* **fix_agent**(`Any`): Fix agent instance, default `None`.

---

## class openjiuwen.auto_harness.contexts.execution.BaseExecutionContext

```python
@dataclass
class BaseExecutionContext:
    """Shared execution context surface."""
```

Shared execution context base class. Provides common capabilities for artifact read/write, progress message construction, and stage result output. All contexts access the global artifact store through the `orchestrator` reference.

**Fields**:
* **orchestrator**(`AutoHarnessOrchestrator`): Auto Harness orchestrator reference.

### task_id

```python
@property
def task_id(self) -> str
```

The task identifier for the current context. The base class implementation returns an empty string; subclasses may override.

**Returns**: Task identifier string.

---

### get_artifact(name: str, default: Any = None) -> Any

Get an artifact value, delegated to the orchestrator's artifact store.

**Parameters**:
* **name**(`str`): Artifact name.
* **default**(`Any`): Default value when not found, default `None`.

**Returns**: The artifact value, or `default`.

---

### require_artifact(name: str) -> Any

Get an artifact value, raising an exception if it does not exist.

**Parameters**:
* **name**(`str`): Artifact name.

**Returns**: The artifact value.

**Raises**: `KeyError` — Raised when the artifact does not exist.

---

### put_artifact(name: str, value: Any) -> None

Store a single artifact.

**Parameters**:
* **name**(`str`): Artifact name.
* **value**(`Any`): Artifact value.

---

### put_artifacts(artifacts: dict[str, Any]) -> None

Batch store artifacts.

**Parameters**:
* **artifacts**(`dict[str, Any]`): Mapping of artifact names to values.

---

### message(text: str, stage: str = '') -> OutputSchema

```python
@staticmethod
def message(text: str, *, stage: str = '') -> OutputSchema
```

Construct a progress message `OutputSchema`.

**Parameters**:
* **text**(`str`): Message text content.
* **stage**(`str`): Associated stage name, default empty.

**Returns**: An `OutputSchema` instance of type `"message"`.

---

### stage_result_output(stage: str, status: str, error: str = '', messages: list[str] | None = None, metrics: dict[str, Any] | None = None) -> OutputSchema

```python
@staticmethod
def stage_result_output(
    *,
    stage: str,
    status: str,
    error: str = '',
    messages: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> OutputSchema
```

Construct a stage result `OutputSchema`.

**Parameters**:
* **stage**(`str`): Stage name.
* **status**(`str`): Stage status (e.g., `"success"` / `"failed"`).
* **error**(`str`): Error message, default empty.
* **messages**(`list[str] | None`): Additional message list, default `None`.
* **metrics**(`dict[str, Any] | None`): Metrics data, default `None`.

**Returns**: An `OutputSchema` instance of type `"stage_result"`.

---

## class openjiuwen.auto_harness.contexts.execution.SessionContext

```python
@dataclass
class SessionContext(BaseExecutionContext):
    """Runtime context passed into session pipelines and stages."""
```

Runtime context passed into session-level pipelines and stages. Inherits all capabilities from `BaseExecutionContext`; `task_id` returns an empty string (session-level has no task scope).

---

## class openjiuwen.auto_harness.contexts.execution.TaskContext

```python
@dataclass
class TaskContext(SessionContext):
    """Runtime context passed into task pipelines and stages."""
```

Runtime context passed into task-level pipelines and stages. Inherits from `SessionContext`, additionally holding the current task and runtime dependencies; `task_id` is computed via `task_key(task)`.

**Fields**:
* **task**(`OptimizationTask`): The current optimization task.
* **runtime**(`TaskRuntime`): Pre-prepared task runtime dependencies.

### task_id

```python
@property
def task_id(self) -> str
```

Returns the artifact scope key for the current task, equivalent to `task_key(self.task)`.

**Returns**: Task identifier string.
