# openjiuwen.agent_evolving.agent_rl.agent_runtime

## class openjiuwen.agent_evolving.agent_rl.rl_rail.RLRail

```python
class openjiuwen.agent_evolving.agent_rl.rl_rail.RLRail(
    session_id: str = "",
    source: str = "offline",
    case_id: Optional[str] = None,
    **kwargs,
)
```

继承 `EvolutionRail`，用于 RL 轨迹收集。基类负责记录 LLM/工具步并在 `after_invoke` 时构建 `Trajectory` 并写入 `trajectory_store`。`RLRail` 在 LLM 步上补充 RL 相关 `meta`，并缓存工具结果供下游使用。

**Usage**（显式传入 store）：

```python
from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryStore

store = InMemoryTrajectoryStore()
rail = RLRail(session_id="s1", source="offline", case_id="c1", trajectory_store=store)
await agent.register_rail(rail)
await agent.invoke({"query": "..."})
# 完整 invoke（含 after_invoke）后，从 `store.query()` 读取轨迹。
```

### priority = 100

Rail 优先级。

### 扩展点

在 `EvolutionRail` 之上重写 `_on_before_invoke`、`_on_after_model_call`、`_on_after_tool_call`。

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.collector.TrajectoryCollector

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.collector.TrajectoryCollector()
```

注册 `RLRail`、运行 Agent，然后从内存 store 读取最后一条已保存的 `Trajectory`；若从未触发持久化则返回 `None`。

### collect(self, agent: Any, inputs: Dict[str, Any], *, session_id: str = "", source: str = "offline", case_id: Optional[str] = None) -> Optional[Trajectory]

运行 Agent；若 rail 生命周期完整执行则返回 `Trajectory`，否则 `None`。

**参数**：

* **agent**：支持 `register_rail` / `unregister_rail` 且可 `invoke`（或兼容 Runner）的 Agent。
* **inputs**：Agent 输入字典（常见用法需包含 `query`）。

**返回**：

**Optional[Trajectory]**，store 中最近保存的一条轨迹；若无则 `None`。

**异常**：

* **ValueError**：Agent 不支持基于 Rail 的轨迹收集。

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.runtime_executor.RuntimeExecutor

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.runtime_executor.RuntimeExecutor(*, task_runner: Optional[TaskRunnerCallable] = None, agent_factory: Optional[Callable[[RLTask], Any]] = None, task_data_fn: Optional[TaskDataFn] = None, reward_fn: Optional[Callable[[RolloutMessage], Any]] = None)
```

自包含的单任务执行器。

支持两种执行模式：
1. **task_runner 模式**：调用者注入 `task_runner(rl_task) -> RolloutMessage` 协程以完全控制执行。
2. **agent 模式**：使用 `agent_factory` + `TrajectoryCollector`（基于 RAIL）运行 Agent 并收集结构化轨迹数据。

### __init__(self, *, task_runner: Optional[TaskRunnerCallable] = None, agent_factory: Optional[Callable[[RLTask], Any]] = None, task_data_fn: Optional[TaskDataFn] = None, reward_fn: Optional[Callable[[RolloutMessage], Any]] = None) -> None

使用可选的任务运行器、Agent 工厂和辅助函数初始化运行时执行器。

### set_task_runner(self, fn: TaskRunnerCallable) -> None

设置任务运行器可调用对象以执行 rollout 任务。

### set_agent_factory(self, factory: Callable[[RLTask], Any]) -> None

设置 Agent 工厂用于为每个任务创建 Agent。

### set_task_data_fn(self, fn: TaskDataFn) -> None

设置将任务样本转换为 Agent 输入的函数。

### set_reward_fn(self, fn: Callable[[RolloutMessage], Any]) -> None

设置计算 rollout 消息奖励的奖励函数。

### execute_async(self, rollout_task: RLTask) -> RolloutMessage

执行 rollout 任务并返回填充好的 RolloutMessage。

**返回**：

**RolloutMessage**，包含完整执行结果的 RolloutMessage 对象。

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.parallel_executor.ParallelRuntimeExecutor

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.parallel_executor.ParallelRuntimeExecutor(data_store: TaskQueue, num_workers: int, *, task_runner: Optional[Callable] = None, agent_factory: Optional[Callable] = None, task_data_fn: Optional[Callable] = None, reward_fn: Optional[Callable] = None)
```

从 TaskQueue 拉取任务的并行 rollout 执行引擎。

每个 worker 创建自己的 RuntimeExecutor 并并发处理任务直到停止。

### __init__(self, data_store: TaskQueue, num_workers: int, *, task_runner: Optional[Callable] = None, agent_factory: Optional[Callable] = None, task_data_fn: Optional[Callable] = None, reward_fn: Optional[Callable] = None) -> None

使用任务队列和 worker 数量初始化并行执行器。

### start(self) -> None

启动所有 worker 循环。

### stop(self) -> None

停止所有 worker 并清理资源。

### is_running(self) -> bool

返回执行器当前是否正在运行。

### set_task_runner(self, fn: Callable) -> None

设置任务运行器可调用对象。

### set_agent_factory(self, factory: Callable) -> None

设置 Agent 工厂。

### set_task_data_fn(self, fn: Callable) -> None

设置任务数据函数。

### set_reward_fn(self, fn: Callable) -> None

设置奖励函数。

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory.AgentFactory

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory.AgentFactory(system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float)
```

为每个 RL 任务创建 DeepAgent 实例的可调用工厂。

必须在首次调用前设置 `proxy_url`（由 MainTrainer 设置）。

### __init__(self, system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float) -> None

初始化 Agent 工厂。

### proxy_url: str | None

代理 URL，必须在创建 Agent 前设置。

### __call__(self, rl_task: RLTask)

为给定的 RL 任务创建并配置 DeepAgent 实例。

**返回**：

配置好的 DeepAgent 实例。

**异常**：

* **BaseError**：proxy_url 未设置时抛出。

## func openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory.build_agent_factory

```python
def build_agent_factory(runtime_cfg: AgentRuntimeConfig, tools: List[Any], tool_names: List[str]) -> AgentFactory
```

从运行时配置和工具构建默认 AgentFactory。

**参数**：

* **runtime_cfg**(AgentRuntimeConfig)：运行时配置。
* **tools**(List[Any])：工具列表。
* **tool_names**(List[str])：工具名称列表。

**返回**：

**AgentFactory**，构建好的 AgentFactory 实例。

**样例**：

```python
>>> from openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory import build_agent_factory
>>> from openjiuwen.agent_evolving.agent_rl.config import AgentRuntimeConfig
>>> 
>>> runtime_cfg = AgentRuntimeConfig(
...     system_prompt="You are a helpful assistant.",
...     temperature=0.7,
...     max_new_tokens=512,
... )
>>> factory = build_agent_factory(runtime_cfg, [], [])
>>> # 设置 proxy_url 后即可使用
>>> factory.proxy_url = "http://localhost:8000/v1"
```
