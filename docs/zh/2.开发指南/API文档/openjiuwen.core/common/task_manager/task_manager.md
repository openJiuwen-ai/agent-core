# openjiuwen.core.common.task_manager

协程任务管理器模块，提供统一的协程任务管理功能，支持任务创建、取消、组管理、层级取消等功能。

## class TaskManager

```python
class TaskManager
```

`TaskManager` 是协程任务的统一管理器，采用单例模式实现，支持结构化并发。使用 WeakValueDictionary 实现自动清理功能。

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

创建并注册一个新的协程任务。

**参数**：

* **coro**(Coroutine)：要作为任务执行的协程
* **task_id**(Optional[str])：可选的自定义任务ID（未提供时自动生成）
* **name**(Optional[str])：可选的任务友好名称
* **group**(Optional[str])：可选的任务组名称，用于任务组织
* **timeout**(Optional[float])：可选的超时时间（秒）
* **metadata**(Optional[Dict])：可选的元数据字典
* **catch_exceptions**(bool)：是否捕获任务中的异常，默认为 False

**返回**：

**Task**，创建的任务对象

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建基础任务
    task = await manager.create_task(async_function())

    # 创建带名称和组的任务
    task = await manager.create_task(
        process_data(),
        name="data_processor",
        group="processors"
    )

    # 创建带超时和元数据的任务
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

创建和管理任务组上下文。

**返回**：

**AsyncGenerator[anyio.abc.TaskGroup, None]**，任务组上下文管理器

**样例**：

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

取消指定任务及其所有子任务（级联取消）。

**参数**：

* **task_id**(str)：要取消的任务ID
* **reason**(str)：取消原因，默认为 "parent_cancelled"

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    # 假设 parent_task 是一个已存在的任务
    await manager.cascade_cancel(parent_task.task_id, reason="user_requested")
```

---

### cancel_group

```python
async def cancel_group(self, group: str) -> int
```

取消指定组中的所有任务。

**参数**：

* **group**(str)：要取消的任务组名称

**返回**：

**int**，被取消的任务数量

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建属于同一组的任务
    await manager.create_task(coro(), group="my_group")
    await manager.create_task(coro(), group="my_group")

    # 取消组中所有任务
    count = await manager.cancel_group("my_group")
    print(f"取消了 {count} 个任务")
```

---

### cancel_all

```python
async def cancel_all(self) -> int
```

取消所有正在运行的任务。

**返回**：

**int**，被取消的任务数量

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建多个任务
    await manager.create_task(task1())
    await manager.create_task(task2())

    # 取消所有任务
    count = await manager.cancel_all()
    print(f"取消了 {count} 个任务")
```

---

### get_task_tree

```python
def get_task_tree(self, task_id: str) -> str
```

获取任务及其后代任务的树形表示（用于调试）。

**参数**：

* **task_id**(str)：根任务的ID

**返回**：

**str**，任务树的字符串表示

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建父任务和子任务
    parent = await manager.create_task(parent_task(), name="parent")
    await manager.create_task(child_task(), name="child1")
    await manager.create_task(child_task(), name="child2")

    # 获取树形表示
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

等待组中所有任务完成。

**参数**：

* **group**(str)：要等待的任务组名称
* **timeout**(Optional[float])：可选的超时时间（秒）
* **return_exceptions**(bool)：如果为 False（默认），则抛出第一个异常并取消其他任务；如果为 True，则返回异常对象

**返回**：

**List[Any]**，组中所有任务的结果列表

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建任务组
    await manager.create_task(async_add(1, 2), group="math")
    await manager.create_task(async_add(3, 4), group="math")

    # 等待组中所有任务完成
    results = await manager.wait_group("math")
    print(f"结果: {results}")  # [3, 7]
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

等待所有任务完成。

**参数**：

* **timeout**(Optional[float])：所有任务的可选超时时间（秒）
* **return_exceptions**(bool)：如果为 False（默认），则抛出第一个异常并取消其他任务；如果为 True，则返回异常对象

**返回**：

**List[Any]**，所有任务的结果列表

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建多个任务
    await manager.create_task(async_work(1))
    await manager.create_task(async_work(2))

    # 等待所有任务完成
    results = await manager.wait_all()
    print(f"所有结果: {results}")
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

返回一个迭代器，按任务完成顺序产生已完成的任务。

**参数**：

* **tasks**(List[Task])：要等待的任务对象列表
* **timeout**(Optional[float])：所有任务的可选超时时间（秒）

**返回**：

**AsyncIterator[Tuple[Task, Any]]**，异步迭代器，产生 (task, result) 元组

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建多个任务
    task1 = await manager.create_task(async_work(1))
    task2 = await manager.create_task(async_work(2))

    # 按完成顺序获取结果
    async for task, result in manager.as_completed([task1, task2]):
        print(f"任务 {task.name} 完成，结果: {result}")
```

---

### on

```python
async def on(self, event_type: str, callback: Callable) -> None
```

注册事件的回调函数。

**参数**：

* **event_type**(str)：事件名称字符串（使用 TaskManagerEvents 常量）
* **callback**(Callable)：要注册的回调函数

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager, TaskManagerEvents

async def on_completed(task):
    print(f"任务 {task.name} 完成!")

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

注册任务创建事件的回调。

---

### on_running

```python
async def on_running(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

注册任务开始运行事件的回调。

---

### on_completed

```python
async def on_completed(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

注册任务完成事件的回调。

---

### on_failed

```python
async def on_failed(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

注册任务失败事件的回调。

---

### on_cancelled

```python
async def on_cancelled(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

注册任务取消事件的回调。

---

### on_timeout

```python
async def on_timeout(self, callback: Callable[[Task], Awaitable[None]]) -> None
```

注册任务超时事件的回调。

---

### off

```python
async def off(self, event_type: str, callback: Callable) -> None
```

注销事件的回调函数。

**参数**：

* **event_type**(str)：事件名称字符串
* **callback**(Callable)：要注销的回调函数

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager, TaskManagerEvents

async def on_completed(task):
    print(f"任务完成!")

async def main():
    manager = get_task_manager()
    await manager.on(TaskManagerEvents.TASK_COMPLETED, on_completed)
    # 稍后注销回调
    await manager.off(TaskManagerEvents.TASK_COMPLETED, on_completed)
```

---

### remove_task

```python
async def remove_task(self, task_id: str) -> bool
```

从注册表中移除指定任务。

**参数**：

* **task_id**(str)：要移除的任务ID

**返回**：

**bool**，如果任务被找到并移除则返回 True，否则返回 False

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(some_task())
    removed = await manager.remove_task(task.task_id)
    print(f"任务已移除: {removed}")
```

---

### remove_completed

```python
async def remove_completed(self) -> int
```

移除所有已完成的任务。

**返回**：

**int**，被移除的已完成任务数量

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()

    # 创建并运行一些任务
    await manager.create_task(some_task())
    await manager.wait_all()

    # 移除所有已完成的任务
    count = await manager.remove_completed()
    print(f"移除了 {count} 个已完成任务")
```

---

## class Task

```python
class Task
```

协程任务数据模型，用于表示和管理单个协程任务。

### wait

```python
async def wait(self) -> Any
```

等待任务完成并返回结果。

**返回**：

**Any**，任务的结果。如果任务抛出异常，则重新抛出该异常

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(async_function())

    # 等待任务完成
    result = await task.wait()
    print(f"任务结果: {result}")
```

---

### cancel

```python
async def cancel(self, cascade: bool = False, reason: Optional[str] = None) -> bool
```

取消此任务。

**参数**：

* **cascade**(bool)：是否同时取消子任务
* **reason**(Optional[str])：取消原因

**返回**：

**bool**，如果触发了取消则返回 True，否则返回 False

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(long_running_task())

    # 取消任务
    cancelled = await task.cancel(reason="user_requested")
    print(f"任务已取消: {cancelled}")
```

---

### abort

```python
def abort(self, reason: Optional[str] = None) -> bool
```

同步中止任务（本地取消，不支持级联）。

**参数**：

* **reason**(Optional[str])：中止原因

**返回**：

**bool**，如果触发了中止则返回 True，否则返回 False

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    manager = get_task_manager()
    task = await manager.create_task(long_running_task())

    # 同步中止任务
    aborted = task.abort(reason="user_requested")
    print(f"任务已中止: {aborted}")
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

执行协程并进行任务生命周期管理。

**参数**：

* **coro**(Coroutine)：要执行的协程
* **callback_trigger**(Optional[Callable])：用于触发事件的回调
* **catch_exceptions**(bool)：如果为 True，则捕获并记录异常而不抛出

**返回**：

**Any**，协程的执行结果

---

## TaskStatus

```python
class TaskStatus(str, Enum)
```

任务状态枚举。

**属性**：

* **PENDING**：任务等待中
* **RUNNING**：任务运行中
* **COMPLETED**：任务已完成
* **FAILED**：任务失败
* **CANCELLED**：任务已取消
* **TIMEOUT**：任务超时

**样例**：

```python
from openjiuwen.core.common.task_manager import TaskStatus

# 检查任务状态
if task.status == TaskStatus.COMPLETED:
    print("任务已完成")
elif task.status == TaskStatus.FAILED:
    print(f"任务失败: {task.error}")
```

---

## TERMINAL_STATES

```python
TERMINAL_STATES: frozenset
```

终止状态集合，包含所有表示任务已终止的状态。

**包含**：

* TaskStatus.COMPLETED
* TaskStatus.FAILED
* TaskStatus.CANCELLED
* TaskStatus.TIMEOUT

**样例**：

```python
from openjiuwen.core.common.task_manager import TERMINAL_STATES

# 检查任务是否已终止
if task.status in TERMINAL_STATES:
    print("任务已终止")
```

---

## TaskError

```python
class TaskError(ExecutionError)
```

任务相关异常的基础类，继承自 ExecutionError。

---

## TaskNotFoundError

```python
class TaskNotFoundError(TaskError)
```

任务未找到时抛出的异常。

**样例**：

```python
from openjiuwen.core.common.task_manager import TaskNotFoundError

try:
    # 尝试获取不存在的任务
    task = registry.get("nonexistent_task_id")
    if not task:
        raise TaskNotFoundError(f"Task not found: {task_id}")
except TaskNotFoundError as e:
    print(f"错误: {e}")
```

---

## DuplicateTaskError

```python
class DuplicateTaskError(TaskError)
```

当任务ID已存在时抛出的异常。

**样例**：

```python
from openjiuwen.core.common.task_manager import DuplicateTaskError

try:
    # 尝试创建重复ID的任务
    task = await manager.create_task(coro(), task_id="existing_id")
except DuplicateTaskError as e:
    print(f"错误: {e}")
```

---

## get_task_manager()

```python
def get_task_manager() -> TaskManager
```

获取全局 TaskManager 单例实例。

**返回**：

**TaskManager**，全局 TaskManager 单例实例

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager

async def main():
    # 获取单例实例
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

创建任务的便捷函数，使用全局 TaskManager。

**参数**：

* **coro**(Coroutine)：要作为任务执行的协程
* **task_id**(Optional[str])：可选的自定义任务ID
* **name**(Optional[str])：可选的任务名称
* **group**(Optional[str])：可选的任务组名称
* **timeout**(Optional[float])：可选的超时时间（秒）
* **metadata**(Optional[Dict])：可选的元数据
* **catch_exceptions**(bool)：是否捕获异常

**返回**：

**Task**，创建的任务对象

**样例**：

```python
from openjiuwen.core.common.task_manager import create_task

async def main():
    # 使用便捷函数创建任务
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

取消整个任务组的便捷函数。

**参数**：

* **group**(str)：要取消的任务组名称

**返回**：

**int**，被取消的任务数量

**样例**：

```python
from openjiuwen.core.common.task_manager import create_task, cancel_group

async def main():
    # 创建任务组
    await create_task(task1(), group="workers")
    await create_task(task2(), group="workers")

    # 取消整个组
    count = await cancel_group("workers")
    print(f"取消了 {count} 个任务")
```

---

## cancel_all()

```python
async def cancel_all() -> int
```

取消所有运行中任务的便捷函数。

**返回**：

**int**，被取消的任务数量

**样例**：

```python
from openjiuwen.core.common.task_manager import create_task, cancel_all

async def main():
    # 创建多个任务
    await create_task(task1())
    await create_task(task2())

    # 取消所有任务
    count = await cancel_all()
    print(f"取消了 {count} 个任务")
```

---

## print_task_tree()

```python
def print_task_tree(task_id: Optional[str] = None) -> None
```

打印任务树用于调试的便捷函数。

**参数**：

* **task_id**(Optional[str])：要打印树的任务ID。如果为 None，则打印所有根任务

**样例**：

```python
from openjiuwen.core.common.task_manager import create_task, print_task_tree

async def main():
    # 创建任务
    task = await create_task(parent_task())
    await create_task(child_task(), parent_id=task.task_id)

    # 打印特定任务树
    print_task_tree(task.task_id)

    # 打印所有任务树
    print_task_tree()
```

---

## get_task_group()

```python
def get_task_group() -> Optional[anyio.abc.TaskGroup]
```

获取当前任务组。

**返回**：

**Optional[anyio.abc.TaskGroup]**，当前任务组，如果未设置则返回 None

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_group

# 获取当前任务组
tg = get_task_group()
if tg:
    print("当前任务组存在")
```

---

## set_task_group()

```python
def set_task_group(tg: Optional[anyio.abc.TaskGroup]) -> Token
```

设置当前任务组。

**参数**：

* **tg**(Optional[anyio.abc.TaskGroup])：要设置的任务组

**返回**：

**Token**，用于恢复的令牌

**样例**：

```python
from openjiuwen.core.common.task_manager import set_task_group
import anyio

# 设置任务组
tg = anyio.create_task_group()
token = set_task_group(tg)

# 稍后恢复
# reset_task_group(token)
```

---

## get_current_task_id()

```python
def get_current_task_id() -> Optional[str]
```

获取当前任务的 ID。

**返回**：

**Optional[str]**，当前任务ID，如果不在任务上下文中则返回 None

**样例**：

```python
from openjiuwen.core.common.task_manager import get_current_task_id

# 获取当前任务ID
task_id = get_current_task_id()
if task_id:
    print(f"当前任务ID: {task_id}")
```

---

## TaskManagerEvents

```python
class TaskManagerEvents(EventBase)
```

任务管理器生命周期事件类型常量。

**属性**：

* **TASK_CREATED**：任务已创建
* **TASK_RUNNING**：任务开始运行
* **TASK_COMPLETED**：任务成功完成
* **TASK_FAILED**：任务失败
* **TASK_CANCELLED**：任务已取消
* **TASK_TIMEOUT**：任务超时

**样例**：

```python
from openjiuwen.core.common.task_manager import get_task_manager
from openjiuwen.core.runner.callback.events import TaskManagerEvents

async def main():
    manager = get_task_manager()

    # 使用事件类型注册回调
    await manager.on(TaskManagerEvents.TASK_COMPLETED, on_completed)
    await manager.on(TaskManagerEvents.TASK_FAILED, on_failed)
```
