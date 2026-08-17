# openjiuwen.agent_evolving.agent_rl.offline.runtime

Offline RL runtime 通过共享 `TrajectorySpanProcessor` 采集 canonical trajectory。在线采集链路不再包含
`TrajectoryCollector` 类，也不由 Rail 持有 trajectory store。

## class RLRail

```python
class RLRail(
    session_id: str = "",
    source: str = "rl_offline",
    case_id: str | None = None,
    *,
    trajectory_span_processor: TrajectorySpanProcessor,
    **kwargs,
)
```

`RLRail` 继承 `EvolutionRail`，复用 processor subscription 和 clean window。RL 通过
`max_trajectory_spans=None` 保留完整 invoke，并把 `source` 和可选 `case_id` 写入投影后的 resource
attributes。

`RLRail` 不持有 builder、Store 或持久 snapshot。注销 Rail 前使用 `get_trajectory()` 读取 detached 值。

## async function run_agent_and_collect_trajectory

```python
async def run_agent_and_collect_trajectory(
    agent: Any,
    inputs: dict[str, Any],
    *,
    trajectory_span_processor: TrajectorySpanProcessor,
    session_id: str = "",
    source: str = "offline",
    case_id: str | None = None,
) -> Trajectory | None: ...
```

该函数：

1. 创建并注册 `EvolutionTriggerPoint.NONE` 的临时 `RLRail`。
2. 创建 Agent session 并执行 Agent。
3. 通过 public clean-window getter 读取轨迹，包括失败时的 partial trajectory 路径。
4. 在 `finally` 中注销 Rail 并关闭 session。

Agent 必须提供 `register_rail()`，并应提供 `unregister_rail()` / `invoke()`。Invoke 失败会记录告警并可能
返回 partial canonical trajectory；不支持 Rail 的 Agent 会抛出 `ValueError`。

## class RuntimeExecutor

```python
class RuntimeExecutor(
    *,
    trajectory_span_processor: TrajectorySpanProcessor,
    agent_factory: Callable[[RLTask], Any] | None = None,
    task_data_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    reward_fn: Callable[[RolloutMessage], Any] | None = None,
)
```

`RuntimeExecutor` 执行一个 `RLTask`。它通过 `agent_factory` 创建 Agent，调用
`run_agent_and_collect_trajectory()`，把 canonical trajectory 转为 rollouts，并按需应用 reward function。

方法：

- `set_agent_factory(factory)`
- `set_task_data_fn(fn)`
- `set_reward_fn(fn)`
- `await execute_async(rollout_task) -> RolloutMessage`

`trajectory_span_processor` 是必填参数，必须属于已初始化的 observability runtime。

## class ParallelRuntimeExecutor

```python
class ParallelRuntimeExecutor(
    data_store: TaskQueue,
    num_workers: int,
    *,
    agent_factory: Callable | None = None,
    task_data_fn: Callable | None = None,
    reward_fn: Callable | None = None,
    observability_config: ObservabilityConfig | None = None,
)
```

`ParallelRuntimeExecutor` 持有共享的 offline rollout 采集边界。`start()` 创建一个
`TrajectorySpanProcessor`，注册到 observability，并传给所有 worker `RuntimeExecutor`；`stop()` 只在当前
executor 创建 provider 时关闭 observability。

方法：

- `await start()`
- `await stop()`
- `is_running() -> bool`
- `set_agent_factory(factory)`
- `set_task_data_fn(fn)`
- `set_reward_fn(fn)`

每个 worker 从 `TaskQueue` 取任务，把 `RolloutMessage` 写回队列，并复用同一个 processor 对象。

## function build_agent_factory

```python
def build_agent_factory(
    *,
    tools: list,
    config: AgentRuntimeConfig,
    ...,
) -> Callable[[RLTask], DeepAgent]: ...
```

构造默认 callable，为每个 RL task 创建一个 `DeepAgent`。返回的 Agent 与
`run_agent_and_collect_trajectory()` 兼容。
