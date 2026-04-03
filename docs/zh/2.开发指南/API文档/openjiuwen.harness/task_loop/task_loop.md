# task_loop

外层任务循环基础设施，驱动 DeepAgent 的多轮自主执行。

---

## class TaskLoopEventHandler

```python
class TaskLoopEventHandler(EventHandler):
    def __init__(self, deep_agent: DeepAgent) -> None: ...
```

驱动外层任务循环的事件处理器。通过 `TaskManager` 创建核心 `Task`，以便 `TaskScheduler` 可以拾取并分派给 `TaskLoopEventExecutor`。

使用每轮 Future 模式：外层循环的每次迭代通过 `prepare_round()` 创建新的 `asyncio.Future`，完成/失败/中止事件解析该 Future。单调递增的 `round_id` 防止过时的完成信号解析错误的 Future。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `last_result` | `Optional[Dict[str, Any]]` | 最近一次 `handle_input` 调用的结果 |
| `interaction_queues` | `Optional[LoopQueues]` | 用于 steer/follow_up 的交互队列 |

---

### prepare_round

```python
def prepare_round(self) -> int
```

为当前轮次创建新 Future。必须在 `publish_event_async` 之前调用。先前未解析的 Future 会被取消。

**返回值**: `int` — 用于关联的 `round_id`。

---

### wait_completion

```python
async def wait_completion(
    self,
    timeout: Optional[float] = None,
) -> Dict[str, Any]
```

等待当前轮次的 Future。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `timeout` | `Optional[float]` | 最大等待秒数。None 表示无限制 |

**返回值**: `Dict[str, Any]` — 结果字典。超时时返回错误字典。

---

### handle_input

```python
async def handle_input(self, inputs: EventHandlerInput) -> Optional[Dict]
```

创建核心 Task 用于调度。不等待完成——外层循环使用 `prepare_round()` + `wait_completion()` 代替。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `inputs` | `EventHandlerInput` | 包含 InputEvent 和 Session |

**返回值**: `Optional[Dict]` — 包含 `status` 和 `task_id` 的确认字典。

---

### handle_task_interaction

```python
async def handle_task_interaction(self, inputs: EventHandlerInput) -> Optional[Dict]
```

处理引导事件，将消息推入 `interaction_queues.steering` 缓冲区。

---

### handle_task_completion

```python
async def handle_task_completion(self, inputs: EventHandlerInput) -> Optional[Dict]
```

向外层循环发出完成信号，解析每轮 Future。

---

### handle_task_failed

```python
async def handle_task_failed(self, inputs: EventHandlerInput) -> Optional[Dict]
```

向外层循环发出失败信号，使用错误字典解析每轮 Future。

---

### handle_follow_up

```python
async def handle_follow_up(self, inputs: EventHandlerInput) -> Optional[Dict]
```

处理后续事件，将消息推入 `interaction_queues.follow_up` 缓冲区。

---

### on_abort

```python
async def on_abort(self) -> None
```

向外层循环发出中止信号，使用错误字典解析当前轮次的 Future。

---

## class TaskLoopEventExecutor

```python
class TaskLoopEventExecutor(TaskExecutor):
    def __init__(
        self,
        dependencies: TaskExecutorDependencies,
        deep_agent: DeepAgent,
    ) -> None: ...
```

委托内部 ReActAgent 执行的任务执行器。管理 `BEFORE_TASK_ITERATION` / `AFTER_TASK_ITERATION` 生命周期回调，处理 TaskPlan 状态更新。

---

### execute_ability

```python
async def execute_ability(
    self,
    task_id: str,
    session: Session,
) -> AsyncIterator[ControllerOutputChunk]
```

通过内部 ReActAgent 执行任务。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `task_id` | `str` | 任务标识符（核心 Task.task_id） |
| `session` | `Session` | 当前会话 |

**返回值**: `AsyncIterator[ControllerOutputChunk]` — 每个输出的控制器输出块。

---

### cancel

```python
async def cancel(self, task_id: str, session: Session) -> bool
```

取消任务，在 TaskPlan 中标记为 `FAILED` 并请求协调器中止。

**返回值**: `bool` — 取消成功返回 True。

---

## function build_deep_executor

```python
def build_deep_executor(
    deep_agent: DeepAgent,
) -> Callable[[TaskExecutorDependencies], TaskLoopEventExecutor]
```

创建用于注册表的构建器工厂。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `deep_agent` | `DeepAgent` | 拥有者 DeepAgent 实例 |

**返回值**: `Callable` — 接受依赖并返回 `TaskLoopEventExecutor` 的可调用对象。

---

## class LoopCoordinator

```python
class LoopCoordinator:
    def __init__(
        self,
        evaluators: Optional[List[StopConditionEvaluator]] = None,
    ) -> None: ...
```

协调外层任务循环的生命周期。跟踪轮次计数、token 用量、挂钟时间和中止标志。`should_continue()` 使用 OR 语义评估 `StopConditionEvaluator` 链。

**属性**:

| 属性 | 类型 | 说明 |
|---|---|---|
| `current_iteration` | `int` | 已完成的轮次数（只读） |
| `is_aborted` | `bool` | 是否已请求中止（只读） |
| `stop_reason` | `Optional[str]` | 触发停止的评估器名称，仍在运行时为 None（只读） |

---

### reset

```python
def reset(self) -> None
```

为新的 invoke 周期重置状态。

---

### increment_iteration

```python
def increment_iteration(self) -> None
```

记录一个已完成的轮次。

---

### add_token_usage

```python
def add_token_usage(self, tokens: int) -> None
```

累积 token 消耗。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `tokens` | `int` | 本轮使用的 token 数 |

---

### set_last_result

```python
def set_last_result(self, result: Dict[str, Any]) -> None
```

存储最近一轮的结果。

---

### request_abort

```python
def request_abort(self) -> None
```

发出信号要求循环立即停止。

---

### should_continue

```python
def should_continue(self) -> bool
```

返回循环是否可以继续。使用 OR 语义评估所有评估器——第一个从 `should_stop()` 返回 True 的评估器终止循环并记录停止原因。

**返回值**: `bool` — 可以继续时返回 True。

---

### get_state / load_state

```python
def get_state(self) -> Dict[str, Any]
def load_state(self, data: Optional[Dict[str, Any]]) -> None
```

导出/恢复 JSON 安全的检查点快照，包含 `iteration`、`token_usage`、`stop_reason` 和每个评估器的状态。

---

## class TaskLoopController

```python
class TaskLoopController(Controller):
    def __init__(self) -> None: ...
```

支持基于轮次的循环的 Controller 子类。封装轮次管理（prepare/wait/complete）、后续队列操作和 DeepAgent 外层任务循环特有的循环退出逻辑。

---

### submit_round

```python
async def submit_round(
    self,
    session: Session,
    query: str,
    is_follow_up: bool = False,
    run_kind: Any = None,
    run_context: Any = None,
) -> None
```

准备一个轮次，构建 `InputEvent` 并发布。封装 `handler.prepare_round()` + 事件构造 + 元数据注入 + 发布。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `session` | `Session` | 当前会话 |
| `query` | `str` | 用户查询文本 |
| `is_follow_up` | `bool` | 本轮是否为后续延续 |
| `run_kind` | `Any` | 心跳支持的运行类型 |
| `run_context` | `Any` | 心跳支持的运行上下文 |

---

### wait_round_completion

```python
async def wait_round_completion(
    self,
    timeout: Optional[float] = None,
) -> Dict[str, Any]
```

等待当前轮次完成。委托给 `event_handler.wait_completion()`。

**参数**:

| 参数 | 类型 | 说明 |
|---|---|---|
| `timeout` | `Optional[float]` | 最大等待秒数 |

**返回值**: `Dict[str, Any]` — 轮次结果字典。

---

### drain_follow_up

```python
def drain_follow_up(self) -> List[str]
```

从处理器队列中排空后续消息。

**返回值**: `List[str]` — 后续消息字符串列表。

---

### has_follow_up

```python
def has_follow_up(self) -> bool
```

检查是否有待处理的后续消息。

**返回值**: `bool` — 有待处理后续消息时返回 True。
