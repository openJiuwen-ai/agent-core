# openjiuwen.core.common.background_tasks

`openjiuwen.core.common.background_tasks` 提供 openJiuwen 中**后台任务**的句柄与工厂函数。当 `task_manager` 的根任务组处于活动状态时，后台协程会注册到 task_manager（享受统一的生命周期、取消与异常捕获）；当 task_manager 不可用时，可回退为原生 `asyncio.Task`。模块对外暴露：

- `BackgroundTask`：后台任务句柄，封装 manager task 与 asyncio task 两种底层形态；
- `create_background_task()` / `start_background_task()`：按需创建后台任务的工厂函数。

> **与 task_manager 的关系**：本模块通过 `sys.modules` 懒加载 `openjiuwen.core.common.task_manager` 下的 `get_task_group()` 与 `create_task()`，避免硬依赖。仅当 task_manager 已被加载且存在活动任务组时，才走 manager 路径；否则按 `fallback_to_asyncio` 决定回退为 asyncio task 或抛错。task_manager 本身的 API 见 [`common/task_manager/task_manager.md`](./task_manager/task_manager.md)。

## class openjiuwen.core.common.background_tasks.BackgroundTask

```
class openjiuwen.core.common.background_tasks.BackgroundTask
```

`BackgroundTask` 是通过 task_manager 创建的后台任务句柄，兼容 manager task 与原生 asyncio task 两种形态。

**特性**：

- 统一句柄：无论底层是 manager task 还是 asyncio task，对外接口一致；
- 延迟就绪：句柄创建时并非立即可用，`_ready` 事件在绑定到底层 task 后才置位；
- 分组归属：每个句柄绑定一个 `group` 名，便于按组管理；
- 取消带超时：`cancel()` 会在指定超时内等待底层 task 终止。

**使用样例**：

```python
>>> from openjiuwen.core.common.background_tasks import create_background_task
>>>
>>> async def poll() -> None:
>>>     ...
>>>
>>> handle = await create_background_task(poll(), name="poll", group="bg")
>>> # ... 做其他工作 ...
>>> await handle.wait()
```

### __init__

```
BackgroundTask(*, group: str)
```

初始化后台任务句柄。**不会绑定任何底层 task**，仅设置分组名并创建就绪事件。

**参数**：

* **group**(str)：任务所属分组名，用于按组管理。

**内部状态**：

- `_group`：分组名；
- `_manager_task`：绑定的 manager task（初始 `None`）；
- `_asyncio_task`：绑定的 asyncio task（初始 `None`）；
- `_ready`：`asyncio.Event`，在 `set_manager_task()` 或 `from_asyncio_task()` 后置位。

> 说明：`__init__` 一般不直接对外使用，建议通过 `create_background_task()` / `start_background_task()` / `BackgroundTask.from_asyncio_task()` 构造句柄。

### classmethod from_asyncio_task

```
@classmethod
def from_asyncio_task(cls, task: asyncio.Task, *, group: str) -> BackgroundTask
```

将一个已存在的 `asyncio.Task` 包装为 `BackgroundTask` 句柄。

**参数**：

* **task**(asyncio.Task)：待包装的 asyncio 任务。
* **group**(str)：任务所属分组名。

**返回**：

* **BackgroundTask**：包装后的句柄，立即就绪（`_ready` 已置位）。

### set_manager_task

```
def set_manager_task(self, task: Task) -> None
```

将句柄绑定到一个 task_manager 的 `Task`，并置位就绪事件。

**参数**：

* **task**(Task)：task_manager 管理的任务对象（`openjiuwen.core.common.task_manager.task.Task`）。

**说明**：通常由 `create_background_task()` 在内部调用，将 manager task 注入句柄。

### group

```
@property
def group(self) -> str
```

返回句柄所属的分组名。

**返回**：

* **str**：分组名。

### done

```
def done(self) -> bool
```

判断底层任务是否已完成。

**返回**：

* **bool**：若绑定的是 manager task，返回其 `is_terminal`；若绑定的是 asyncio task，返回其 `done()`；未绑定任何 task 时返回 `False`。

### async wait

```
async def wait(self) -> Any
```

等待底层任务完成并返回其结果。

**返回**：

* **Any**：manager task 返回 `await task.wait()` 的结果；asyncio task 返回 `await task` 的结果。

**行为说明**：

- 先 `await self._ready.wait()` 等待句柄绑定完成；
- 根据 `_manager_task` / `_asyncio_task` 分别 await 对应底层 task。

### async cancel

```
async def cancel(self, *, reason: str = "background_task_cancelled", timeout: float = 1.0) -> None
```

取消底层任务，并在指定超时内等待其终止。

**参数**：

* **reason**(str, 可选)：取消原因，传给 manager task。默认值：`"background_task_cancelled"`。
* **timeout**(float, 可选)：取消后等待终止的超时时间（秒）。默认值：`1.0`。

**行为说明**：

- 先 `await self._ready.wait()` 等待句柄绑定完成；
- 若绑定 manager task：调用 `task.cancel(reason=reason)`，并在 `timeout` 内 `await task.wait()`（超时则放弃等待）；
- 若绑定 asyncio task：调用 `task.cancel()`，在 `timeout` 内 `await task`，并吞掉 `asyncio.CancelledError`。

## 模块函数

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

在 task_manager 任务组活动时，通过 `create_task()` 创建后台任务；否则按 `fallback_to_asyncio` 回退为 asyncio task。

**参数**：

* **coro**(Coroutine)：待运行的后台协程。
* **name**(str)：任务名。
* **group**(str)：任务所属分组名。
* **fallback_to_asyncio**(bool, 可选)：task_manager 不可用时是否回退为 `asyncio.create_task()`。默认值：`True`。为 `False` 且 task_manager 不可用时抛 `RuntimeError`。

**返回**：

* **BackgroundTask**：已绑定底层 task 的句柄。

**异常**：

* **RuntimeError**：`fallback_to_asyncio=False` 且 task_manager 根任务组不可用时抛出。

**行为说明**：

- 通过 `_get_loaded_task_group()` 探测 task_manager 是否已加载且存在活动任务组；
- manager 路径：`await create_task(coro, name=name, group=group, catch_exceptions=True)`，随后 `handle.set_manager_task(task)`；
- 回退路径：`BackgroundTask.from_asyncio_task(asyncio.create_task(coro), group=group)`。

**样例**：

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

从**同步**生命周期方法中启动后台任务（适配非 async 入口，如组件 `aboutToAppear` 等同步初始化场景）。

**参数**：同 `create_background_task()`。

**返回**：

* **BackgroundTask**：句柄。manager 路径下，底层 task 的绑定是异步完成的（通过 `tg.start_soon(_create)`），故句柄在就绪前即可返回。

**异常**：

* **RuntimeError**：`fallback_to_asyncio=False` 且 task_manager 根任务组不可用（或 manager 未加载）时抛出。

**行为说明**：

- 探测 task_manager 任务组；不可用时按 `fallback_to_asyncio` 回退或抛 `RuntimeError`；
- manager 路径：构造句柄后，用 `tg.start_soon(_create)` 异步地将协程注册为 manager task 并调用 `set_manager_task`；
- 回退路径：`BackgroundTask.from_asyncio_task(asyncio.create_task(coro), group=group)`。

**样例**：

```python
>>> from openjiuwen.core.common.background_tasks import start_background_task
>>>
>>> # 在同步方法中启动后台任务
>>> handle = start_background_task(my_coro(), name="worker", group="bg")
```

## 实现细节：task_manager 懒加载与回退

模块通过两个私有助手实现 task_manager 的**懒加载探测**，避免在未使用 task_manager 的场景下产生硬依赖：

- `_get_loaded_task_group()`：从 `sys.modules` 取 `openjiuwen.core.common.task_manager.context`，若已加载则返回其 `get_task_group()`，否则返回 `None`；
- `_get_loaded_create_task()`：从 `sys.modules` 取 `openjiuwen.core.common.task_manager.manager`，若已加载则返回其 `create_task`，否则返回 `None`。

仅当上述两者均非 `None`（即 task_manager 已加载且存在活动任务组）时，`create_background_task()` / `start_background_task()` 才走 manager 路径；否则按 `fallback_to_asyncio` 决定回退为 `asyncio.Task` 或抛 `RuntimeError("task manager root task group is not available")`。

**判定优先级**：

1. `_get_loaded_task_group()` 为 `None` → task_manager 未加载或无活动任务组；
2. `_get_loaded_create_task()` 为 `None` → manager 模块未加载；
3. 两者均非 `None` → 走 manager 路径（`create_task` + `set_manager_task`）；
4. 否则按 `fallback_to_asyncio` 回退或抛错。
