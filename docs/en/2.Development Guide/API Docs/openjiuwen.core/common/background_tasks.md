# openjiuwen.core.common.background_tasks

`openjiuwen.core.common.background_tasks` provides **background task** handles and factory functions in openJiuwen. When the root task group of `task_manager` is active, background coroutines are registered with the task_manager (enjoying unified lifecycle, cancellation, and exception capture); when task_manager is unavailable, it falls back to native `asyncio.Task`. The module exposes:

- `BackgroundTask`: A background task handle that encapsulates both manager task and asyncio task underlying forms;
- `create_background_task()` / `start_background_task()`: Factory functions that create background tasks on demand.

> **Relationship with task_manager**: This module lazily loads `get_task_group()` and `create_task()` from `openjiuwen.core.common.task_manager` via `sys.modules` to avoid hard dependencies. The manager path is taken only when task_manager has been loaded and an active task group exists; otherwise, it falls back to an asyncio task or raises an error based on `fallback_to_asyncio`. For the task_manager API itself, see [`common/task_manager/task_manager.md`](./task_manager/task_manager.md).

## class openjiuwen.core.common.background_tasks.BackgroundTask

```
class openjiuwen.core.common.background_tasks.BackgroundTask
```

`BackgroundTask` is a background task handle created via task_manager, compatible with both manager task and native asyncio task forms.

**Features**:

- Unified handle: Consistent external interface regardless of whether the underlying task is a manager task or asyncio task;
- Deferred readiness: The handle is not immediately usable upon creation; the `_ready` event is set only after binding to the underlying task;
- Group membership: Each handle is bound to a `group` name for group-based management;
- Cancellation with timeout: `cancel()` waits for the underlying task to terminate within the specified timeout.

**Usage Example**:

```python
>>> from openjiuwen.core.common.background_tasks import create_background_task
>>>
>>> async def poll() -> None:
>>>     ...
>>>
>>> handle = await create_background_task(poll(), name="poll", group="bg")
>>> # ... do other work ...
>>> await handle.wait()
```

### __init__

```
BackgroundTask(*, group: str)
```

Initializes the background task handle. **Does not bind any underlying task**; only sets the group name and creates the readiness event.

**Parameters**:

* **group** (str): The group name for the task, used for group-based management.

**Internal State**:

- `_group`: Group name;
- `_manager_task`: Bound manager task (initially `None`);
- `_asyncio_task`: Bound asyncio task (initially `None`);
- `_ready`: `asyncio.Event`, set after `set_manager_task()` or `from_asyncio_task()`.

> Note: `__init__` is generally not intended for direct external use. It is recommended to construct handles via `create_background_task()` / `start_background_task()` / `BackgroundTask.from_asyncio_task()`.

### classmethod from_asyncio_task

```
@classmethod
def from_asyncio_task(cls, task: asyncio.Task, *, group: str) -> BackgroundTask
```

Wraps an existing `asyncio.Task` into a `BackgroundTask` handle.

**Parameters**:

* **task** (asyncio.Task): The asyncio task to wrap.
* **group** (str): The group name for the task.

**Returns**:

* **BackgroundTask**: The wrapped handle, immediately ready (`_ready` is set).

### set_manager_task

```
def set_manager_task(self, task: Task) -> None
```

Binds the handle to a task_manager `Task` and sets the readiness event.

**Parameters**:

* **task** (Task): The task object managed by task_manager (`openjiuwen.core.common.task_manager.task.Task`).

**Note**: Usually called internally by `create_background_task()` to inject the manager task into the handle.

### group

```
@property
def group(self) -> str
```

Returns the group name of the handle.

**Returns**:

* **str**: The group name.

### done

```
def done(self) -> bool
```

Checks whether the underlying task has completed.

**Returns**:

* **bool**: If bound to a manager task, returns its `is_terminal`; if bound to an asyncio task, returns its `done()`; returns `False` if no task is bound.

### async wait

```
async def wait(self) -> Any
```

Waits for the underlying task to complete and returns its result.

**Returns**:

* **Any**: For manager task, returns the result of `await task.wait()`; for asyncio task, returns the result of `await task`.

**Behavior**:

- First `await self._ready.wait()` to wait for handle binding to complete;
- Then awaits the corresponding underlying task based on `_manager_task` / `_asyncio_task`.

### async cancel

```
async def cancel(self, *, reason: str = "background_task_cancelled", timeout: float = 1.0) -> None
```

Cancels the underlying task and waits for its termination within the specified timeout.

**Parameters**:

* **reason** (str, optional): Cancellation reason, passed to the manager task. Default: `"background_task_cancelled"`.
* **timeout** (float, optional): Timeout in seconds to wait for termination after cancellation. Default: `1.0`.

**Behavior**:

- First `await self._ready.wait()` to wait for handle binding to complete;
- If bound to a manager task: calls `task.cancel(reason=reason)` and `await task.wait()` within `timeout` (gives up waiting on timeout);
- If bound to an asyncio task: calls `task.cancel()`, `await task` within `timeout`, and swallows `asyncio.CancelledError`.

## Module Functions

### async create_background_task

```
async def create_background_task(
    coro: Coroutine,
    *,
    name: str,
    group: str,
    fallback_to_asyncio: bool = True,
) -> BackgroundTask
```

Creates a background task via `create_task()` when the task_manager task group is active; otherwise falls back to an asyncio task based on `fallback_to_asyncio`.

**Parameters**:

* **coro** (Coroutine): The background coroutine to run.
* **name** (str): Task name.
* **group** (str): Group name for the task.
* **fallback_to_asyncio** (bool, optional): Whether to fall back to `asyncio.create_task()` when task_manager is unavailable. Default: `True`. Raises `RuntimeError` when `False` and task_manager is unavailable.

**Returns**:

* **BackgroundTask**: A handle bound to the underlying task.

**Exceptions**:

* **RuntimeError**: Raised when `fallback_to_asyncio=False` and the task_manager root task group is unavailable.

**Behavior**:

- Probes whether task_manager is loaded and has an active task group via `_get_loaded_task_group()`;
- Manager path: `await create_task(coro, name=name, group=group, catch_exceptions=True)`, then `handle.set_manager_task(task)`;
- Fallback path: `BackgroundTask.from_asyncio_task(asyncio.create_task(coro), group=group)`.

**Example**:

```python
>>> from openjiuwen.core.common.background_tasks import create_background_task
>>>
>>> handle = await create_background_task(my_coro(), name="worker", group="bg")
```

### start_background_task

```
def start_background_task(
    coro: Coroutine,
    *,
    name: str,
    group: str,
    fallback_to_asyncio: bool = True,
) -> BackgroundTask
```

Starts a background task from a **synchronous** lifecycle method (adapting non-async entry points, such as synchronous initialization scenarios like component `aboutToAppear`).

**Parameters**: Same as `create_background_task()`.

**Returns**:

* **BackgroundTask**: The handle. In the manager path, the underlying task binding is completed asynchronously (via `tg.start_soon(_create)`), so the handle can be returned before it is ready.

**Exceptions**:

* **RuntimeError**: Raised when `fallback_to_asyncio=False` and the task_manager root task group is unavailable (or the manager is not loaded).

**Behavior**:

- Probes the task_manager task group; falls back based on `fallback_to_asyncio` or raises `RuntimeError` when unavailable;
- Manager path: After constructing the handle, uses `tg.start_soon(_create)` to asynchronously register the coroutine as a manager task and calls `set_manager_task`;
- Fallback path: `BackgroundTask.from_asyncio_task(asyncio.create_task(coro), group=group)`.

**Example**:

```python
>>> from openjiuwen.core.common.background_tasks import start_background_task
>>>
>>> # Start a background task in a synchronous method
>>> handle = start_background_task(my_coro(), name="worker", group="bg")
```

## Implementation Details: Lazy Loading and Fallback for task_manager

The module implements **lazy loading probe** of task_manager through two private helpers, avoiding hard dependencies in scenarios where task_manager is not used:

- `_get_loaded_task_group()`: Retrieves `openjiuwen.core.common.task_manager.context` from `sys.modules`; returns its `get_task_group()` if loaded, otherwise `None`;
- `_get_loaded_create_task()`: Retrieves `openjiuwen.core.common.task_manager.manager` from `sys.modules`; returns its `create_task` if loaded, otherwise `None`.

`create_background_task()` / `start_background_task()` take the manager path only when both of the above are non-`None` (i.e., task_manager is loaded and an active task group exists); otherwise, they fall back to `asyncio.Task` or raise `RuntimeError("task manager root task group is not available")` based on `fallback_to_asyncio`.

**Resolution Priority**:

1. `_get_loaded_task_group()` is `None` → task_manager is not loaded or has no active task group;
2. `_get_loaded_create_task()` is `None` → the manager module is not loaded;
3. Both are non-`None` → take the manager path (`create_task` + `set_manager_task`);
4. Otherwise, fall back or raise an error based on `fallback_to_asyncio`.
