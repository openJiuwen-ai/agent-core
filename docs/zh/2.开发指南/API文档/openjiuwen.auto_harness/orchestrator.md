# openjiuwen.auto_harness.orchestrator

Auto Harness 编排器模块，负责会话控制与顶层 pipeline 调度。`AutoHarnessOrchestrator` 是整个 auto harness 运行时的入口控制器，管理 pipeline 选择、stage 执行、预算控制、经验库持久化、Git 操作与 CI 门控等子系统。

子模块：
- `orchestrator`：会话编排器与工厂函数。

---

## class openjiuwen.auto_harness.orchestrator.AutoHarnessOrchestrator

```
class openjiuwen.auto_harness.orchestrator.AutoHarnessOrchestrator
```

会话控制器与顶层 pipeline 调度器。负责初始化所有运行子系统（经验库、预算控制器、Fix Loop 控制器、Worktree 管理器、Git 操作、CI 门控等），驱动 pipeline 选择与执行，并对外暴露统一的流式会话接口。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置实例。
* **agent**(`Optional[DeepAgent]`)：可选的 DeepAgent 实例；若未传入，会尝试从 `stream_rails` 中推断。
* **stream_rails**(`Optional[List[AgentRail]]`)：可选的流式 Rail 列表，用于在 agent 回调中注入横切逻辑。

**样例**：
```python
>>> from openjiuwen.auto_harness.schema import AutoHarnessConfig
>>> from openjiuwen.auto_harness.orchestrator import AutoHarnessOrchestrator
>>> config = AutoHarnessConfig(data_dir="/tmp/auto_harness")
>>> orchestrator = AutoHarnessOrchestrator(config)
```

### \_\_init\_\_(self, config: AutoHarnessConfig, agent: Optional['DeepAgent'] = None, \*, stream_rails: Optional[List['AgentRail']] = None) -> None

初始化编排器，创建所有运行子系统并绑定 `CancellationRail`。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置实例。
* **agent**(`Optional[DeepAgent]`)：可选的 DeepAgent 实例。
* **stream_rails**(`Optional[List[AgentRail]]`)：可选的流式 Rail 列表。

### cancel(self) -> None

请求编排器停止执行。由服务层在收到取消请求时调用。Pipeline 在迭代边界检查 `should_cancel`，`CancellationRail` 在 agent 回调中检查。

**返回**：`None`

### should_cancel

```
@property
should_cancel -> bool
```

返回是否已请求取消。Pipeline 应在 task 迭代边界检查此属性，类似于 `budget.should_stop` 的检查方式。

**返回**：`bool` —— 若已请求取消则返回 `True`。

### results

```
@property
results -> list[CycleResult]
```

返回当前会话的最新结果列表。

**返回**：`list[CycleResult]` —— 所有已记录的 task 周期结果副本。

### last_cycle_result

```
@property
last_cycle_result -> CycleResult
```

返回最近一次 task 周期结果。

**返回**：`CycleResult`

### record_cycle_result(self, result: CycleResult) -> None

在编排器上持久化一条 task 周期结果。

**参数**：
* **result**(`CycleResult`)：要记录的周期结果。

**返回**：`None`

### message_output(self, text: str) -> OutputSchema

构造一条消息类型的 `OutputSchema`。

**参数**：
* **text**(`str`)：消息文本内容。

**返回**：`OutputSchema` —— 类型为 `"message"` 的输出对象。

### create_interaction(self, interaction_id: str) -> 'asyncio.Future[Any]'

为某个 stage 创建一个待处理的交互 future。外部调用方可通过 `run_session_stream(message=...)` 来 resolve 该 future。

**参数**：
* **interaction_id**(`str`)：交互的唯一标识符。

**返回**：`asyncio.Future[Any]` —— 等待外部输入的 future 对象。

### run_session_stream(self, tasks: Optional[List[OptimizationTask]] = None, \*, message: Optional[dict[str, Any]] = None) -> AsyncIterator[Any]

统一的外部会话执行与交互 API。

当传入 `message` 时，将其分发到内部处理器（如 resolve 一个待处理的交互），并返回空的异步迭代器。

否则运行完整的会话 pipeline，返回 `OutputSchema` 数据块的异步迭代器。

**参数**：
* **tasks**(`Optional[List[OptimizationTask]]`)：可选的优化任务列表。
* **message**(`Optional[dict[str, Any]]`)：可选的交互消息字典，包含 `interaction_id` 等字段。

**返回**：`AsyncIterator[Any]` —— `OutputSchema` 数据块的异步迭代器。

**样例**：
```python
>>> async for chunk in orchestrator.run_session_stream(tasks=my_tasks):
...     print(chunk)
```

### ensure_session_runtime_dir(self) -> Path

返回当前会话的 runtime extension 目录，若不存在则自动创建。

**返回**：`Path` —— 会话级 runtime extension 目录路径。

---

## openjiuwen.auto_harness.orchestrator.create_auto_harness_orchestrator

```
create_auto_harness_orchestrator(
    config: AutoHarnessConfig,
    *,
    agent: Optional['DeepAgent'] = None,
    stream_rails: Optional[List['AgentRail']] = None,
) -> AutoHarnessOrchestrator
```

创建编排器实例的工厂函数。

**参数**：
* **config**(`AutoHarnessConfig`)：Auto Harness 配置实例。
* **agent**(`Optional[DeepAgent]`)：可选的 DeepAgent 实例。
* **stream_rails**(`Optional[List[AgentRail]]`)：可选的流式 Rail 列表。

**返回**：`AutoHarnessOrchestrator` —— 新创建的编排器实例。

**样例**：
```python
>>> from openjiuwen.auto_harness.schema import AutoHarnessConfig
>>> from openjiuwen.auto_harness.orchestrator import create_auto_harness_orchestrator
>>> config = AutoHarnessConfig(data_dir="/tmp/auto_harness")
>>> orchestrator = create_auto_harness_orchestrator(config)
```
