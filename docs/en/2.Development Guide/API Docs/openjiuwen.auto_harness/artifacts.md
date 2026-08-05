# openjiuwen.auto_harness.artifacts

Artifact storage primitives module for data passing between stages in the Auto Harness pipeline. Implements artifact isolation through a dual-level namespace of session and task scopes, supporting cross-stage sharing of intermediate results.

Submodules:
- `store`: Artifact storage implementation

---

## class openjiuwen.auto_harness.artifacts.store.ArtifactStore

```python
@dataclass
class ArtifactStore:
    """A scoped artifact store with session and task namespaces."""
```

Artifact store with session and task namespaces. Session-level artifacts are visible to all tasks; task-level artifacts are only visible under the specified `task_id`. On read, task-level is checked first, falling back to session-level if not found.

**Fields**:
* **_session**(`dict[str, Any]`): Session-level artifact dictionary, default empty dict.
* **_task**(`dict[str, dict[str, Any]]`): Task-level artifact dictionary, bucketed by `task_id`, default empty dict.

### get(name: str, task_id: str = '', default: Any = None) -> Any

Get an artifact value. If `task_id` is specified, checks task-level first, falling back to session-level if not found; if `task_id` is not specified, only checks session-level.

**Parameters**:
* **name**(`str`): Artifact name.
* **task_id**(`str`): Task identifier, default empty string (session-level only).
* **default**(`Any`): Default return value when not found, default `None`.

**Returns**: The artifact value, or `default`.

---

### require(name: str, task_id: str = '') -> Any

Get an artifact value, raising `KeyError` if it does not exist.

**Parameters**:
* **name**(`str`): Artifact name.
* **task_id**(`str`): Task identifier, default empty string.

**Returns**: The artifact value.

**Raises**: `KeyError` — Raised when the artifact does not exist.

---

### put(name: str, value: Any, task_id: str = '') -> None

Store a single artifact. If `task_id` is specified, stores in the task-level namespace; otherwise stores in the session-level namespace.

**Parameters**:
* **name**(`str`): Artifact name.
* **value**(`Any`): Artifact value.
* **task_id**(`str`): Task identifier, default empty string (stores in session-level).

---

### put_many(artifacts: dict[str, Any], task_id: str = '') -> None

Batch store artifacts. Equivalent to calling `put` for each key-value pair in the dictionary.

**Parameters**:
* **artifacts**(`dict[str, Any]`): Mapping of artifact names to values.
* **task_id**(`str`): Task identifier, default empty string.

---

### has(name: str, task_id: str = '') -> bool

Check if an artifact exists. Lookup logic is the same as `get`.

**Parameters**:
* **name**(`str`): Artifact name.
* **task_id**(`str`): Task identifier, default empty string.

**Returns**: `True` if the artifact exists, `False` if it does not.

---

### reset_task(task_id: str) -> None

Clear all artifacts for a specified task.

**Parameters**:
* **task_id**(`str`): The task identifier to clear.
