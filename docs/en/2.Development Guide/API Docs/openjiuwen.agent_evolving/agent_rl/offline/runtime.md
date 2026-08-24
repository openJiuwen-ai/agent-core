# openjiuwen.agent_evolving.agent_rl.offline.runtime

Offline RL runtime APIs collect canonical trajectories through a shared `TrajectorySpanProcessor`. There is no
`TrajectoryCollector` class or Rail-owned trajectory store in the online capture path.

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

`RLRail` extends `EvolutionRail` and uses its processor subscription and clean window. RL capture keeps the complete
invoke by setting `max_trajectory_spans=None`, then adds `source` and optional `case_id` to projected resource
attributes.

`RLRail` does not own a builder, store, or persistent snapshot. Read the detached value with `get_trajectory()` before
unregistering the Rail.

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

The function:

1. Creates and registers a temporary `RLRail` with `EvolutionTriggerPoint.NONE`.
2. Creates an Agent session and invokes the Agent.
3. Reads the public clean-window getter, including the partial-trajectory failure path.
4. Unregisters the Rail and closes the session in `finally` blocks.

The Agent must provide `register_rail()` and should provide `unregister_rail()` / `invoke()`. Invoke failures are logged
and may return a partial canonical trajectory; an unsupported Agent raises `ValueError`.

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

`RuntimeExecutor` executes one `RLTask`. It creates the Agent through `agent_factory`, calls
`run_agent_and_collect_trajectory()`, converts the canonical trajectory to rollouts, and optionally applies a reward
function.

Methods:

- `set_agent_factory(factory)`
- `set_task_data_fn(fn)`
- `set_reward_fn(fn)`
- `await execute_async(rollout_task) -> RolloutMessage`

`trajectory_span_processor` is required and must already belong to an initialized observability runtime.

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

`ParallelRuntimeExecutor` owns the shared offline rollout capture boundary. `start()` creates one
`TrajectorySpanProcessor`, registers it with observability, and passes it to every worker `RuntimeExecutor`.
`stop()` shuts down observability only when this executor created the provider.

Methods:

- `await start()`
- `await stop()`
- `is_running() -> bool`
- `set_agent_factory(factory)`
- `set_task_data_fn(fn)`
- `set_reward_fn(fn)`

Each worker pulls tasks from `TaskQueue`, writes `RolloutMessage` results back to the queue, and reuses the same
processor object.

## function build_agent_factory

```python
def build_agent_factory(
    *,
    tools: list,
    config: AgentRuntimeConfig,
    ...,
) -> Callable[[RLTask], DeepAgent]: ...
```

Build the default callable that creates one `DeepAgent` per RL task. The returned Agent is compatible with
`run_agent_and_collect_trajectory()`.
