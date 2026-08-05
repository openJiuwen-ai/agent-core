# openjiuwen.auto_harness.orchestrator

Auto Harness orchestrator module, responsible for session control and top-level pipeline dispatching. `AutoHarnessOrchestrator` is the entry controller for the entire auto harness runtime, managing pipeline selection, stage execution, budget control, experience store persistence, Git operations, CI gating, and other subsystems.

Submodules:
- `orchestrator`: Session orchestrator and factory function.

---

## class openjiuwen.auto_harness.orchestrator.AutoHarnessOrchestrator

```
class openjiuwen.auto_harness.orchestrator.AutoHarnessOrchestrator
```

Session controller and top-level pipeline dispatcher. Initializes all runtime subsystems (experience store, budget controller, fix loop controller, worktree manager, Git operations, CI gating, etc.), drives pipeline selection and execution, and exposes a unified streaming session interface.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration instance.
* **agent**(`Optional[DeepAgent]`): Optional DeepAgent instance; if not provided, attempts to infer from `stream_rails`.
* **stream_rails**(`Optional[List[AgentRail]]`): Optional list of streaming rails for injecting cross-cutting logic into agent callbacks.

**Example**:
```python
>>> from openjiuwen.auto_harness.schema import AutoHarnessConfig
>>> from openjiuwen.auto_harness.orchestrator import AutoHarnessOrchestrator
>>> config = AutoHarnessConfig(data_dir="/tmp/auto_harness")
>>> orchestrator = AutoHarnessOrchestrator(config)
```

### \_\_init\_\_(self, config: AutoHarnessConfig, agent: Optional['DeepAgent'] = None, \*, stream_rails: Optional[List['AgentRail']] = None) -> None

Initialize the orchestrator, creating all runtime subsystems and binding `CancellationRail`.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration instance.
* **agent**(`Optional[DeepAgent]`): Optional DeepAgent instance.
* **stream_rails**(`Optional[List[AgentRail]]`): Optional list of streaming rails.

### cancel(self) -> None

Request the orchestrator to stop execution. Called by the service layer upon receiving a cancellation request. Pipelines check `should_cancel` at iteration boundaries; `CancellationRail` checks it in agent callbacks.

**Returns**: `None`

### should_cancel

```
@property
should_cancel -> bool
```

Returns whether cancellation has been requested. Pipelines should check this property at task iteration boundaries, similar to `budget.should_stop`.

**Returns**: `bool` — `True` if cancellation has been requested.

### results

```
@property
results -> list[CycleResult]
```

Returns the latest result list for the current session.

**Returns**: `list[CycleResult]` — A copy of all recorded task cycle results.

### last_cycle_result

```
@property
last_cycle_result -> CycleResult
```

Returns the most recent task cycle result.

**Returns**: `CycleResult`

### record_cycle_result(self, result: CycleResult) -> None

Persist a task cycle result on the orchestrator.

**Parameters**:
* **result**(`CycleResult`): The cycle result to record.

**Returns**: `None`

### message_output(self, text: str) -> OutputSchema

Construct a message-type `OutputSchema`.

**Parameters**:
* **text**(`str`): The message text content.

**Returns**: `OutputSchema` — An output object of type `"message"`.

### create_interaction(self, interaction_id: str) -> 'asyncio.Future[Any]'

Create a pending interaction future for a stage. External callers can resolve this future via `run_session_stream(message=...)`.

**Parameters**:
* **interaction_id**(`str`): Unique identifier for the interaction.

**Returns**: `asyncio.Future[Any]` — A future object awaiting external input.

### run_session_stream(self, tasks: Optional[List[OptimizationTask]] = None, \*, message: Optional[dict[str, Any]] = None) -> AsyncIterator[Any]

Unified external session execution and interaction API.

When `message` is provided, dispatches it to the internal handler (e.g., resolving a pending interaction) and returns an empty async iterator.

Otherwise, runs the full session pipeline and returns an async iterator of `OutputSchema` chunks.

**Parameters**:
* **tasks**(`Optional[List[OptimizationTask]]`): Optional list of optimization tasks.
* **message**(`Optional[dict[str, Any]]`): Optional interaction message dict, containing fields such as `interaction_id`.

**Returns**: `AsyncIterator[Any]` — An async iterator of `OutputSchema` chunks.

**Example**:
```python
>>> async for chunk in orchestrator.run_session_stream(tasks=my_tasks):
...     print(chunk)
```

### ensure_session_runtime_dir(self) -> Path

Returns the runtime extension directory for the current session, creating it if it does not exist.

**Returns**: `Path` — The session-level runtime extension directory path.

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

Factory function for creating an orchestrator instance.

**Parameters**:
* **config**(`AutoHarnessConfig`): Auto Harness configuration instance.
* **agent**(`Optional[DeepAgent]`): Optional DeepAgent instance.
* **stream_rails**(`Optional[List[AgentRail]]`): Optional list of streaming rails.

**Returns**: `AutoHarnessOrchestrator` — A newly created orchestrator instance.

**Example**:
```python
>>> from openjiuwen.auto_harness.schema import AutoHarnessConfig
>>> from openjiuwen.auto_harness.orchestrator import create_auto_harness_orchestrator
>>> config = AutoHarnessConfig(data_dir="/tmp/auto_harness")
>>> orchestrator = create_auto_harness_orchestrator(config)
```
