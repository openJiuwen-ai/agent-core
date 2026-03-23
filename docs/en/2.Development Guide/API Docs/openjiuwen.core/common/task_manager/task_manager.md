# openjiuwen.core.common.task_manager

Coroutine task manager module that provides unified coroutine task management with support for task creation, cancellation, group management, hierarchical cancellation, and more.

## class TaskManager

```python
class TaskManager
```

`TaskManager` is the unified manager for coroutine tasks, implemented as a singleton with support for structured concurrency. Uses WeakValueDictionary for automatic cleanup.

### create_task

```python
async def create_task(
    self,
    coro: Coroutine,
    *,
    task_id: Optional[str] = None,
    name: Optional[str] = None,
    group: Optional[str] = None,
    timeout: Optional[float] = None,
    metadata: Optional[Dict] = None,
    catch_exceptions: bool = False,
) -> Task
```

Creates and registers a new coroutine task.

**Parameters**:

* **coro** (Coroutine): The coroutine to execute as a task
* **task_id** (Optional[str]): Optional custom task ID (auto-generated if not provided)
* **name** (Optional[str]): Optional friendly name for the task
* **group** (Optional[str]): Optional group name for task organization
* **timeout** (Optional[float]): Optional timeout in seconds
* **metadata** (Optional[Dict]): Optional dictionary of metadata to attach to the task
* **catch_exceptions** (bool): Whether to catch exceptions in the task, defaults to False

**Returns**:

**Task**, the created task object

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create a basic task
    task = await manager.create_task(async_function())

    # Create a task with name and group
    task = await manager.create_task(
        process_data(),
        name="data_processor",
        group="processors"
    )

    # Create a task with timeout and metadata
    task = await manager.create_task(
        fetch_data(),
        name="data_fetcher",
        timeout=30.0,
        metadata={"priority": "high", "retry": 3}
    )
```

---

### task_group

```python
@asynccontextmanager
async def task_group(self) -> AsyncGenerator[anyio.abc.TaskGroup, None]
```

Creates and manages a task group context.

**Returns**:

**AsyncGenerator[anyio.abc.TaskGroup, None]**, the task group context manager

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    async with manager.task_group() as tg:
        await manager.create_task(coro1())
        await manager.create_task(coro2())
```

---

### cascade_cancel

```python
async def cascade_cancel(self, task_id: str, reason: str = "parent_cancelled") -> None
```

Cancels the specified task and all its children recursively.

**Parameters**:

* **task_id** (str): The ID of the task to cancel
* **reason** (str): The reason for cancellation, defaults to "parent_cancelled"

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    # Assuming parent_task is an existing task
    await manager.cascade_cancel(parent_task.task_id, reason="user_requested")
```

---

### cancel_group

```python
async def cancel_group(self, group: str) -> int
```

Cancels all tasks in a specific group.

**Parameters**:

* **group** (str): The group name to cancel

**Returns**:

**int**, the number of tasks that were cancelled

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create tasks with group
    await manager.create_task(coro(), group="my_group")
    await manager.create_task(coro(), group="my_group")

    # Cancel all tasks in the group
    count = await manager.cancel_group("my_group")
    print(f"Cancelled {count} tasks")
```

---

### cancel_all

```python
async def cancel_all(self) -> int
```

Cancels all running tasks managed by this manager.

**Returns**:

**int**, the number of tasks that were cancelled

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create several tasks
    await manager.create_task(task1())
    await manager.create_task(task2())

    # Cancel all tasks
    count = await manager.cancel_all()
    print(f"Cancelled {count} tasks")
```

---

### get_task_tree

```python
def get_task_tree(self, task_id: str) -> str
```

Gets a tree representation of a task and its descendants (for debugging).

**Parameters**:

* **task_id** (str): The ID of the root task

**Returns**:

**str**, a string representation of the task tree

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create parent and child tasks
    parent = await manager.create_task(parent_task(), name="parent")
    await manager.create_task(child_task(), name="child1")
    await manager.create_task(child_task(), name="child2")

    # Get tree representation
    tree = manager.get_task_tree(parent.task_id)
    print(tree)
```

---

### wait_group

```python
async def wait_group(
    self,
    group: str,
    timeout: Optional[float] = None,
    return_exceptions: bool = False,
) -> List[Any]
```

Waits for all tasks in a group to complete.

**Parameters**:

* **group** (str): The group name to wait for
* **timeout** (Optional[float]): Optional timeout in seconds
* **return_exceptions** (bool): If False (default), raises the first exception and cancels other tasks. If True, returns exception objects instead

**Returns**:

**List[Any]**, list of results from all tasks in the group

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create tasks with group
    await manager.create_task(async_add(1, 2), group="math")
    await manager.create_task(async_add(3, 4), group="math")

    # Wait for all tasks in the group to complete
    results = await manager.wait_group("math")
    print(f"Results: {results}")  # [3, 7]
```

---

### wait_all

```python
async def wait_all(
    self,
    timeout: Optional[float] = None,
    return_exceptions: bool = False,
) -> List[Any]
```

Waits for all tasks managed by this manager to complete.

**Parameters**:

* **timeout** (Optional[float]): Optional timeout in seconds for all tasks
* **return_exceptions** (bool): If False (default), raises the first exception and cancels other tasks. If True, returns exception objects instead

**Returns**:

**List[Any]**, list of results from all tasks

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create several tasks
    await manager.create_task(async_work(1))
    await manager.create_task(async_work(2))

    # Wait for all tasks to complete
    results = await manager.wait_all()
    print(f"All results: {results}")
```

---

### as_completed

```python
async def as_completed(
    self,
    tasks: List[Task],
    timeout: Optional[float] = None
) -> AsyncIterator[Tuple[Task, Any]]
```

Returns an iterator that yields completed tasks as they finish.

**Parameters**:

* **tasks** (List[Task]): List of Task objects to wait on
* **timeout** (Optional[float]): Optional timeout in seconds for all tasks

**Returns**:

**AsyncIterator[Tuple[Task, Any]]**, async iterator that yields (task, result) tuples

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create multiple tasks
    task1 = await manager.create_task(async_work(1))
    task2 = await manager.create_task(async_work(2))

    # Get results as tasks complete
    async for task, result in manager.as_completed([task1, task2]):
        print(f"Task {task.name} completed, result: {result}")
```

---

### on

```python
async def on(self, event_type: str, callback: Callable) -> None
```

Registers a callback function for an event.

**Parameters**:

* **event_type** (str): Event name string (use TaskManagerEvents constants)
* **callback** (Callable): Callback function to register

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager, TaskManagerEvents

async def on_completed(task):
    print(f"Task {task.name} completed!")

async def main():
    manager = get_task_manager()
    await manager.on(TaskManagerEvents.TASK_COMPLETED, on_completed)
    await manager.create_task(some_task(), name="my_task")
```

---

### on_created

```python
async def on_created(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

Registers a callback for the task created event.

---

### on_running

```python
async def on_running(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

Registers a callback for the task running event.

---

### on_completed

```python
async def on_completed(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

Registers a callback for the task completed event.

---

### on_failed

```python
async def on_failed(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

Registers a callback for the task failed event.

---

### on_cancelled

```python
async def on_cancelled(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

Registers a callback for the task cancelled event.

---

### on_timeout

```python
async def on_timeout(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

Registers a callback for the task timeout event.

---

### off

```python
async def off(self, event_type: str, callback: Callable) -> None
```

Unregisters a callback function from an event.

**Parameters**:

* **event_type** (str): Event name string
* **callback** (Callable): Callback function to unregister

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager, TaskManagerEvents

async def on_completed(task):
    print(f"Task completed!")

async def main():
    manager = get_task_manager()
    await manager.on(TaskManagerEvents.TASK_COMPLETED, on_completed)
    # Later, unregister the callback
    await manager.off(TaskManagerEvents.TASK_COMPLETED, on_completed)
```

---

### remove_task

```python
async def remove_task(self, task_id: str) -> bool
```

Removes a task from the registry.

**Parameters**:

* **task_id** (str): The ID of the task to remove

**Returns**:

**bool**, True if the task was found and removed, False otherwise

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(some_task())
    removed = await manager.remove_task(task.task_id)
    print(f"Task removed: {removed}")
```

---

### remove_completed

```python
async def remove_completed(self) -> int
```

Removes all completed tasks from the registry.

**Returns**:

**int**, the number of completed tasks removed

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # Create and run some tasks
    await manager.create_task(some_task())
    await manager.wait_all()

    # Remove all completed tasks
    count = await manager.remove_completed()
    print(f"Removed {count} completed tasks")
```

---

## class Task

```python
class Task
```

Coroutine task data model for representing and managing a single coroutine task.

### wait

```python
async def wait(self) -> Any
```

Waits for the task to complete and returns the result.

**Returns**:

**Any**, the result of the task. If the task raised an exception, that exception is re-raised

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(async_function())

    # Wait for task to complete
    result = await task.wait()
    print(f"Task result: {result}")
```

---

### cancel

```python
async def cancel(self, cascade: bool = False, reason: Optional[str] = None) -> bool
```

Cancels this task.

**Parameters**:

* **cascade** (bool): Whether to also cancel child tasks
* **reason** (Optional[str]): The reason for cancellation

**Returns**:

**bool**, True if cancellation was triggered, False otherwise

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(long_running_task())

    # Cancel the task
    cancelled = await task.cancel(reason="user_requested")
    print(f"Task cancelled: {cancelled}")
```

---

### abort

```python
def abort(self, reason: Optional[str] = None) -> bool
```

Synchronously aborts the task (local cancellation only, no cascade support).

**Parameters**:

* **reason** (Optional[str]): The reason for abort

**Returns**:

**bool**, True if abort was triggered, False otherwise

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(long_running_task())

    # Abort the task synchronously
    aborted = task.abort(reason="user_requested")
    print(f"Task aborted: {aborted}")
```

---

### execute

```python
async def execute(
    self,
    coro: Coroutine,
    callback_trigger: Optional[Callable] = None,
    catch_exceptions: bool = False,
) -> Any
```

Executes the coroutine with task lifecycle management.

**Parameters**:

* **coro** (Coroutine): The coroutine to execute
* **callback_trigger** (Optional[Callable]): Optional callback for triggering events
* **catch_exceptions** (bool): If True, catch and log exceptions instead of raising

**Returns**:

**Any**, the result of the coroutine execution

---

## TaskStatus

```python
class TaskStatus(str, Enum)
```

Task status enumeration.

**Attributes**:

* **PENDING**: Task is pending
* **RUNNING**: Task is running
* **COMPLETED**: Task completed successfully
* **FAILED**: Task failed
* **CANCELLED**: Task was cancelled
* **TIMEOUT**: Task timed out

**Example**:

```python
from openjiuwen.core.common.task_manager import TaskStatus

# Check task status
if task.status == TaskStatus.COMPLETED:
    print("Task completed")
elif task.status == TaskStatus.FAILED:
    print(f"Task failed: {task.error}")
```

---

## TERMINAL_STATES

```python
TERMINAL_STATES: frozenset
```

Terminal states set containing all states that represent a terminated task.

**Contains**:

* TaskStatus.COMPLETED
* TaskStatus.FAILED
* TaskStatus.CANCELLED
* TaskStatus.TIMEOUT

**Example**:

```python
from openjiuwen.core.common.task_manager import TERMINAL_STATES

# Check if task is terminal
if task.status in TERMINAL_STATES:
    print("Task is terminal")
```

---

## TaskError

```python
class TaskError(ExecutionError)
```

Base exception class for task-related errors, inherits from ExecutionError.

---

## TaskNotFoundError

```python
class TaskNotFoundError(TaskError)
```

Exception raised when a task is not found.

**Example**:

```python
from openjiuwen.core.common.task_manager import TaskNotFoundError

try:
    # Try to get a non-existent task
    task = registry.get("nonexistent_task_id")
    if not task:
        raise TaskNotFoundError(f"Task not found: {task_id}")
except TaskNotFoundError as e:
    print(f"Error: {e}")
```

---

## DuplicateTaskError

```python
class DuplicateTaskError(TaskError)
```

Exception raised when a task with the same ID already exists.

**Example**:

```python
from openjiuwen.core.common.task_manager import DuplicateTaskError

try:
    # Try to create a task with duplicate ID
    task = await manager.create_task(coro(), task_id="existing_id")
except DuplicateTaskError as e:
    print(f"Error: {e}")
```

---

## get_task_manager()

```python
def get_task_manager() -> TaskManager
```

Gets the global TaskManager singleton instance.

**Returns**:

**TaskManager**, the global TaskManager singleton instance

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    # Get singleton instance
    manager = get_task_manager()
    task = await manager.create_task(async_work())
```

---

## create_task()

```python
async def create_task(
    coro: Coroutine,
    *,
    task_id: Optional[str] = None,
    name: Optional[str] = None,
    group: Optional[str] = None,
    timeout: Optional[float] = None,
    metadata: Optional[Dict] = None,
    catch_exceptions: bool = False,
) -> Task
```

Convenience function to create a task using the global TaskManager.

**Parameters**:

* **coro** (Coroutine): The coroutine to execute as a task
* **task_id** (Optional[str]): Optional custom task ID
* **name** (Optional[str]): Optional task name
* **group** (Optional[str]): Optional task group name
* **timeout** (Optional[float]): Optional timeout in seconds
* **metadata** (Optional[Dict]): Optional metadata
* **catch_exceptions** (bool): Whether to catch exceptions

**Returns**:

**Task**, the created task object

**Example**:

```python
from openjiuwen.core.common.task_manager import create_task

async def main():
    # Use convenience function to create task
    task = await create_task(
        fetch_data(),
        name="data_fetcher",
        group="io_tasks",
        timeout=30.0
    )
```

---

## cancel_group()

```python
async def cancel_group(group: str) -> int
```

Convenience function to cancel an entire task group.

**Parameters**:

* **group** (str): The group name to cancel

**Returns**:

**int**, the number of tasks cancelled

**Example**:

```python
from openjiuwen.core.common.task_manager import create_task, cancel_group

async def main():
    # Create a task group
    await create_task(task1(), group="workers")
    await create_task(task2(), group="workers")

    # Cancel the entire group
    count = await cancel_group("workers")
    print(f"Cancelled {count} tasks")
```

---

## cancel_all()

```python
async def cancel_all() -> int
```

Convenience function to cancel all running tasks.

**Returns**:

**int**, the number of tasks cancelled

**Example**:

```python
from openjiuwen.core.common.task_manager import create_task, cancel_all

async def main():
    # Create multiple tasks
    await create_task(task1())
    await create_task(task2())

    # Cancel all tasks
    count = await cancel_all()
    print(f"Cancelled {count} tasks")
```

---

## print_task_tree()

```python
def print_task_tree(task_id: Optional[str] = None) -> None
```

Convenience function to print the task tree for debugging.

**Parameters**:

* **task_id** (Optional[str]): The task ID to print tree for. If None, prints all root tasks

**Example**:

```python
from openjiuwen.core.common.task_manager import create_task, print_task_tree

async def main():
    # Create tasks
    task = await create_task(parent_task())
    await create_task(child_task(), parent_id=task.task_id)

    # Print specific task tree
    print_task_tree(task.task_id)

    # Print all task trees
    print_task_tree()
```

---

## get_task_group()

```python
def get_task_group() -> Optional[anyio.abc.TaskGroup]
```

Gets the current task group.

**Returns**:

**Optional[anyio.abc.TaskGroup]**, the current task group, or None if not set

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_group

# Get current task group
tg = get_task_group()
if tg:
    print("Current task group exists")
```

---

## set_task_group()

```python
def set_task_group(tg: Optional[anyio.abc.TaskGroup]) -> Token
```

Sets the current task group.

**Parameters**:

* **tg** (Optional[anyio.abc.TaskGroup]): The task group to set

**Returns**:

**Token**, token for restoration

**Example**:

```python
from openjiuwen.core.common.task_manager import set_task_group
import anyio

# Set task group
tg = anyio.create_task_group()
token = set_task_group(tg)

# Later restore
# reset_task_group(token)
```

---

## get_current_task_id()

```python
def get_current_task_id() -> Optional[str]
```

Gets the current task's ID.

**Returns**:

**Optional[str]**, the current task ID, or None if not in a task context

**Example**:

```python
from openjiuwen.core.common.task_manager import get_current_task_id

# Get current task ID
task_id = get_current_task_id()
if task_id:
    print(f"Current task ID: {task_id}")
```

---

## TaskManagerEvents

```python
class TaskManagerEvents(EventBase)
```

Task manager lifecycle event type constants.

**Attributes**:

* **TASK_CREATED**: Task was created
* **TASK_RUNNING**: Task started running
* **TASK_COMPLETED**: Task completed successfully
* **TASK_FAILED**: Task failed with an error
* **TASK_CANCELLED**: Task was cancelled
* **TASK_TIMEOUT**: Task timed out

**Example**:

```python
from openjiuwen.core.common.task_manager import get_task_manager
from openjiuwen.core.runner.callback.events import TaskManagerEvents

async def main():
    manager = get_task_manager()

    # Register callbacks using event types
    await manager.on(TaskManagerEvents.TASK_COMPLETED, on_completed)
    await manager.on(TaskManagerEvents.TASK_FAILED, on_failed)
```
