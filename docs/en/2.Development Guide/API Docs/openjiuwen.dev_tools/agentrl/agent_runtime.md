# openjiuwen.dev_tools.agentrl.agent_runtime

## class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollectionRail

```
class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollectionRail()
```

AgentRail lifecycle hook-based LLM call trajectory data collector.

- before_model_call: Record serialized input messages and tools
- after_model_call: Record serialized LLM response → create Rollout
- after_tool_call: Record actual tool results keyed by tool_call_id

**Usage**:

```python
rail = TrajectoryCollectionRail()
await agent.register_rail(rail)
await agent.invoke({"query": "..."})
rollouts = rail.get_rollouts()
rail.clear()
await agent.unregister_rail(rail)
```

### priority = 100

Rail priority.

### __init__(self) -> None

Initialize trajectory collection rail.

### async before_model_call(self, ctx: AgentCallbackContext) -> None

Serialize input messages and tools; patch tool messages with actual content.

### async after_model_call(self, ctx: AgentCallbackContext) -> None

Serialize LLM response and commit current turn as Rollout.

### async after_tool_call(self, ctx: AgentCallbackContext) -> None

Capture raw tool results to overwrite incorrect serialized content in context messages.

### def get_rollouts(self) -> List[Rollout]

Return a copy of all Rollout objects collected for the current run.

### def clear(self) -> None

Clear all collected rollouts and internal state for a new collection.

---

## class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollector

```
class openjiuwen.dev_tools.agentrl.agent_runtime.trajectory.TrajectoryCollector()
```

Wrapper that runs Agent via TrajectoryCollectionRail and collects trajectory data.

### async collect(self, agent: Any, inputs: Dict[str, Any]) -> List[Rollout]

Run Agent and return list of Rollout objects (one per LLM turn).

**Parameters**:

* **agent**: ReActAgent (or any Agent) that supports register_rail/unregister_rail.
* **inputs**: Agent input dict (must contain 'query').

**Returns**:

**List[Rollout]**, List of Rollout objects collected during the run.

**Exceptions**:

* **ValueError**: Agent does not support rail-based trajectory collection.

---

## class openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor.RuntimeExecutor

```
class openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor.RuntimeExecutor(*, task_runner: Optional[TaskRunnerCallable] = None, agent_factory: Optional[Callable[[RLTask], Any]] = None, task_data_fn: Optional[TaskDataFn] = None, reward_fn: Optional[Callable[[RolloutMessage], Any]] = None)
```

Self-contained single-task executor.

Supports two execution modes:
1. **task_runner mode**: Caller injects `task_runner(rl_task) -> RolloutMessage` coroutine for full control.
2. **agent mode**: Uses `agent_factory` + `TrajectoryCollector` (based on RAIL) to run Agent and collect structured trajectory data.

### __init__(self, *, task_runner: Optional[TaskRunnerCallable] = None, agent_factory: Optional[Callable[[RLTask], Any]] = None, task_data_fn: Optional[TaskDataFn] = None, reward_fn: Optional[Callable[[RolloutMessage], Any]] = None) -> None

Initialize runtime executor with optional task runner, Agent factory, and helper functions.

### def set_task_runner(self, fn: TaskRunnerCallable) -> None

Set task runner callable to execute rollout tasks.

### def set_agent_factory(self, factory: Callable[[RLTask], Any]) -> None

Set Agent factory for creating an Agent per task.

### def set_task_data_fn(self, fn: TaskDataFn) -> None

Set the function that converts task samples to Agent input.

### def set_reward_fn(self, fn: Callable[[RolloutMessage], Any]) -> None

Set reward function that computes rewards for rollout messages.

### async execute_async(self, rollout_task: RLTask) -> RolloutMessage

Execute rollout task and return filled RolloutMessage.

**Returns**:

**RolloutMessage**, RolloutMessage with complete execution result.

---

## class openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor.ParallelRuntimeExecutor

```
class openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor.ParallelRuntimeExecutor(data_store: TaskQueue, num_workers: int, *, task_runner: Optional[Callable] = None, agent_factory: Optional[Callable] = None, task_data_fn: Optional[Callable] = None, reward_fn: Optional[Callable] = None)
```

Parallel rollout execution engine that pulls tasks from TaskQueue.

Each worker creates its own RuntimeExecutor and processes tasks concurrently until stopped.

### __init__(self, data_store: TaskQueue, num_workers: int, *, task_runner: Optional[Callable] = None, agent_factory: Optional[Callable] = None, task_data_fn: Optional[Callable] = None, reward_fn: Optional[Callable] = None) -> None

Initialize parallel executor with task queue and worker count.

### async start(self) -> None

Start all worker loops.

### async stop(self) -> None

Stop all workers and clean up resources.

### def is_running(self) -> bool

Return whether executor is currently running.

### def set_task_runner(self, fn: Callable) -> None

Set task runner callable.

### def set_agent_factory(self, factory: Callable) -> None

Set Agent factory.

### def set_task_data_fn(self, fn: Callable) -> None

Set task data function.

### def set_reward_fn(self, fn: Callable) -> None

Set reward function.

---

## class openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory.AgentFactory

```
class openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory.AgentFactory(system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float)
```

Callable factory that creates ReActAgent instances for each RL task.

Must set `proxy_url` before first use (set by MainTrainer).

### __init__(self, system_prompt: str, tools: List[Any], tool_names: List[str], temperature: float, max_new_tokens: int, top_p: float, presence_penalty: float, frequency_penalty: float) -> None

Initialize Agent factory.

### proxy_url: str | None

Proxy URL; must be set before creating an Agent.

### def __call__(self, rl_task: RLTask)

Create and configure a ReActAgent instance for the given RL task.

**Returns**:

Configured ReActAgent instance.

**Exceptions**:

* **BaseError**: Raised when proxy_url is not set.

---

## func openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory.build_agent_factory

```
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
>>> from openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory import build_agent_factory
>>> from openjiuwen.dev_tools.agentrl.config.schemas import AgentRuntimeConfig
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
