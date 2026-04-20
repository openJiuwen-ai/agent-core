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

Extends `EvolutionRail` for RL trajectory collection. The base class records LLM/tool steps and, on `after_invoke`, builds a `Trajectory` and saves it to `trajectory_store`. `RLRail` adds RL-oriented `meta` on LLM steps and captures tool results for patching.

**Usage** (with an explicit store):

```python
from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail
from openjiuwen.agent_evolving.trajectory import InMemoryTrajectoryStore

store = InMemoryTrajectoryStore()
rail = RLRail(session_id="s1", source="offline", case_id="c1", trajectory_store=store)
await agent.register_rail(rail)
await agent.invoke({"query": "..."})
# After a full invoke (including after_invoke), read trajectories from `store.query()`.
```

### priority = 100

Rail priority.

### Extension points

Overrides `_on_before_invoke`, `_on_after_model_call`, and `_on_after_tool_call` on top of `EvolutionRail`.

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.collector.TrajectoryCollector

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.collector.TrajectoryCollector()
```

Wrapper that registers `RLRail`, runs the agent, then reads the last saved `Trajectory` from an in-memory store (or returns `None` if `after_invoke` never persisted a trajectory).

### collect(self, agent: Any, inputs: Dict[str, Any], *, session_id: str = "", source: str = "offline", case_id: Optional[str] = None) -> Optional[Trajectory]

Run Agent and return a `Trajectory` when the rail lifecycle completes; otherwise `None`.

**Parameters**:

* **agent**: Agent that supports `register_rail` / `unregister_rail` and `invoke` (or compatible runner).
* **inputs**: Agent input dict (must contain `query` for typical flows).

**Returns**:

**Optional[Trajectory]**, the most recently saved trajectory, or `None` if none was written to the store.

**Exceptions**:

* **ValueError**: Agent does not support rail-based trajectory collection.

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.runtime_executor.RuntimeExecutor

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.runtime_executor.RuntimeExecutor(*, task_runner: Optional[TaskRunnerCallable] = None, agent_factory: Optional[Callable[[RLTask], Any]] = None, task_data_fn: Optional[TaskDataFn] = None, reward_fn: Optional[Callable[[RolloutMessage], Any]] = None)
```

Self-contained single-task executor.

Supports two execution modes:
1. **task_runner mode**: Caller injects `task_runner(rl_task) -> RolloutMessage` coroutine for full control.
2. **agent mode**: Uses `agent_factory` + `TrajectoryCollector` (based on RAIL) to run Agent and collect structured trajectory data.

### __init__(self, *, task_runner: Optional[TaskRunnerCallable] = None, agent_factory: Optional[Callable[[RLTask], Any]] = None, task_data_fn: Optional[TaskDataFn] = None, reward_fn: Optional[Callable[[RolloutMessage], Any]] = None) -> None

Initialize runtime executor with optional task runner, Agent factory, and helper functions.

### set_task_runner(self, fn: TaskRunnerCallable) -> None

Set task runner callable to execute rollout tasks.

### set_agent_factory(self, factory: Callable[[RLTask], Any]) -> None

Set Agent factory for creating an Agent per task.

### set_task_data_fn(self, fn: TaskDataFn) -> None

Set the function that converts task samples to Agent input.

### set_reward_fn(self, fn: Callable[[RolloutMessage], Any]) -> None

Set reward function that computes rewards for rollout messages.

### execute_async(self, rollout_task: RLTask) -> RolloutMessage

Execute rollout task and return filled RolloutMessage.

**Returns**:

**RolloutMessage**, RolloutMessage with complete execution result.

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.parallel_executor.ParallelRuntimeExecutor

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.parallel_executor.ParallelRuntimeExecutor(data_store: TaskQueue, num_workers: int, *, task_runner: Optional[Callable] = None, agent_factory: Optional[Callable] = None, task_data_fn: Optional[Callable] = None, reward_fn: Optional[Callable] = None)
```

Parallel rollout execution engine that pulls tasks from TaskQueue.

Each worker creates its own RuntimeExecutor and processes tasks concurrently until stopped.

### __init__(self, data_store: TaskQueue, num_workers: int, *, task_runner: Optional[Callable] = None, agent_factory: Optional[Callable] = None, task_data_fn: Optional[Callable] = None, reward_fn: Optional[Callable] = None) -> None

Initialize parallel executor with task queue and worker count.

### start(self) -> None

Start all worker loops.

### stop(self) -> None

Stop all workers and clean up resources.

### is_running(self) -> bool

Return whether executor is currently running.

### set_task_runner(self, fn: Callable) -> None

Set task runner callable.

### set_agent_factory(self, factory: Callable) -> None

Set Agent factory.

### set_task_data_fn(self, fn: Callable) -> None

Set task data function.

### set_reward_fn(self, fn: Callable) -> None

Set reward function.

## class openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory.AgentFactory

```python
class openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory.AgentFactory(system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float)
```

Callable factory that creates ReActAgent instances for each RL task.

Must set `proxy_url` before first use (set by MainTrainer).

### __init__(self, system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float) -> None

Initialize Agent factory.

### proxy_url: str | None

Proxy URL; must be set before creating an Agent.

### __call__(self, rl_task: RLTask)

Create and configure a ReActAgent instance for the given RL task.

**Returns**:

Configured ReActAgent instance.

**Exceptions**:

* **BaseError**: Raised when proxy_url is not set.

## func openjiuwen.agent_evolving.agent_rl.offline.runtime.agent_factory.build_agent_factory

```python
def build_agent_factory(runtime_cfg: AgentRuntimeConfig, tools: List[Any], tool_names: List[str]) -> AgentFactory
```

Build default AgentFactory from runtime config and tools.

**Parameters**:

* **runtime_cfg**(AgentRuntimeConfig): Runtime config.
* **tools**(List[Any]): Tool list.
* **tool_names**(List[str]): Tool name list.

**Returns**:

**AgentFactory**, Built AgentFactory instance.

**Example**:

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
>>> # After setting proxy_url it can be used
>>> factory.proxy_url = "http://localhost:8000/v1"
```
