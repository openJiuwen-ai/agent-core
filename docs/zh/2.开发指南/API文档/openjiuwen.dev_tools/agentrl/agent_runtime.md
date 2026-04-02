# openjiuwen.dev_tools.agentrl.agent_runtime

## class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollectionRail

```python
class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollectionRail()
```

基于 AgentRail 生命周期钩子的 LLM 调用轨迹数据收集器。

- before_model_call：记录序列化的输入消息和工具
- after_model_call：记录序列化的 LLM 响应 → 创建 Rollout
- after_tool_call：记录按 tool_call_id 键控的实际工具结果

**Usage**：

```python
rail = TrajectoryCollectionRail()
await agent.register_rail(rail)
await agent.invoke({"query": "..."})
rollouts = rail.get_rollouts()
rail.clear()
await agent.unregister_rail(rail)
```

### priority = 100

Rail 优先级。

### __init__(self) -> None

初始化轨迹收集 Rail。

### before_model_call(self, ctx: AgentCallbackContext) -> None

序列化输入消息和工具；用实际内容修补工具消息。

### after_model_call(self, ctx: AgentCallbackContext) -> None

序列化 LLM 响应并将当前轮次提交为 Rollout。

### after_tool_call(self, ctx: AgentCallbackContext) -> None

捕获原始工具结果以覆盖上下文消息中错误的序列化内容。

### get_rollouts(self) -> List[Rollout]

返回当前运行收集的所有 Rollout 对象的副本。

### clear(self) -> None

清除所有已收集的 rollouts 和内部状态以进行新的收集。

## class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollector

```python
class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollector()
```

通过 TrajectoryCollectionRail 运行 Agent 并收集轨迹数据的封装器。

### collect(self, agent: Any, inputs: Dict[str, Any]) -> List[Rollout]

运行 Agent 并返回 Rollout 对象列表（每个 LLM 轮次一个）。

**参数**：

* **agent**：支持 register_rail/unregister_rail 的 ReActAgent（或任何 Agent）。
* **inputs**：Agent 输入字典（必须包含 'query'）。

**返回**：

**List[Rollout]**，运行期间收集的 Rollout 对象列表。

**异常**：

* **ValueError**：Agent 不支持基于 Rail 的轨迹收集。

## class openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor.RuntimeExecutor

```python
class openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor.RuntimeExecutor(*, task_runner: Optional[TaskRunnerCallable] = None, agent_factory: Optional[Callable[[RLTask], Any]] = None, task_data_fn: Optional[TaskDataFn] = None, reward_fn: Optional[Callable[[RolloutMessage], Any]] = None)
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

## class openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor.ParallelRuntimeExecutor

```python
class openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor.ParallelRuntimeExecutor(data_store: TaskQueue, num_workers: int, *, task_runner: Optional[Callable] = None, agent_factory: Optional[Callable] = None, task_data_fn: Optional[Callable] = None, reward_fn: Optional[Callable] = None)
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

## class openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory.AgentFactory

```python
class openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory.AgentFactory(system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float)
```

为每个 RL 任务创建 ReActAgent 实例的可调用工厂。

必须在首次调用前设置 `proxy_url`（由 MainTrainer 设置）。

### __init__(self, system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float) -> None

初始化 Agent 工厂。

### proxy_url: str | None

代理 URL，必须在创建 Agent 前设置。

### __call__(self, rl_task: RLTask)

为给定的 RL 任务创建并配置 ReActAgent 实例。

**返回**：

配置好的 ReActAgent 实例。

**异常**：

* **BaseError**：proxy_url 未设置时抛出。

## func openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory.build_agent_factory

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
>>> from openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory import build_agent_factory
>>> from openjiuwen.dev_tools.agentrl.config.schemas import AgentRuntimeConfig
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
