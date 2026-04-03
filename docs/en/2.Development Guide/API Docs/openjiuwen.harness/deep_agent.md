# openjiuwen.harness.deep_agent

## class openjiuwen.harness.DeepAgent

```python
class DeepAgent(BaseAgent)
```

The primary agent harness runtime. Wraps a `ReActAgent` with a task loop, workspace, sub-agents, rails, and prompt assembly to form a fully-featured autonomous agent capable of handling complex, multi-step tasks.

Use the [`create_deep_agent`](./factory.md#function-openjiuwenharnesscreate_deep_agent) factory for convenient construction, or instantiate directly and call `configure()`.

**Attributes**:

- **deep_config** ([DeepAgentConfig](./schema/config.md#class-openjiuwenharnessschemadeepagentconfig)): The resolved configuration for this agent.
- **react_agent** (ReActAgent): The inner `ReActAgent` that executes tool-calling rounds.
- **loop_coordinator** ([LoopCoordinator](./task_loop/task_loop.md#class-openjiuwenharnesstask_looploopcoordinator)): Tracks iteration count, token usage, abort requests, and continuation decisions.
- **loop_controller** ([TaskLoopController](./task_loop/task_loop.md#class-openjiuwenharnesstask_looptaskloopcontroller)): Submits rounds, drains follow-ups, and coordinates round completion.
- **event_queue** (PriorityQueue): Internal priority queue for loop events (follow-ups, steers, aborts).
- **event_handler** ([TaskLoopEventHandler](./task_loop/task_loop.md#class-openjiuwenharnesstask_looptaskloopteventhandler)): Processes loop events such as task interactions, completions, and failures.
- **is_initialized** (bool): Whether `configure()` has been called.
- **is_invoke_active** (bool): Whether an `invoke()` or `stream()` call is currently in progress.

### method configure

```python
configure(config: DeepAgentConfig) -> DeepAgent
```

Initialize the agent with the given configuration. Sets up the inner `ReActAgent`, task loop, workspace, rails, tools, sub-agents, and prompt builder.

**Parameters**:

- **config** ([DeepAgentConfig](./schema/config.md#class-openjiuwenharnessschemadeepagentconfig)): Full agent configuration.

**Returns**:

**DeepAgent**: `self`, for method chaining.

### async method invoke

```python
async invoke(
    inputs: dict,
    session: Session,
) -> Dict
```

Run the agent to completion on the given inputs and return the final result.

**Parameters**:

- **inputs** (dict): Input dictionary, typically `{"input": "user message"}`.
- **session** (Session): The session instance for state persistence and streaming.

**Returns**:

**Dict**: Result dictionary containing the agent output.

### async method stream

```python
async stream(
    inputs: dict,
    session: Session,
    stream_modes: list[str] | None = None,
) -> AsyncIterator
```

Run the agent and yield intermediate streaming events.

**Parameters**:

- **inputs** (dict): Input dictionary.
- **session** (Session): The session instance.
- **stream_modes** (list[str], optional): Stream mode filters. Default: `None`.

**Returns**:

**AsyncIterator**: An async iterator of streaming events.

### async method follow_up

```python
async follow_up(
    msg: str,
    task_id: str | None,
    session: Session,
) -> None
```

Enqueue a follow-up message for the currently running task loop.

**Parameters**:

- **msg** (str): The follow-up message content.
- **task_id** (str | None): Optional task ID to target a specific task.
- **session** (Session): The current session.

### async method steer

```python
async steer(
    msg: str,
    session: Session,
) -> None
```

Enqueue a steering message that redirects the agent mid-run.

**Parameters**:

- **msg** (str): The steering instruction.
- **session** (Session): The current session.

### async method abort

```python
async abort(
    session: Session,
) -> None
```

Request the agent to abort the current task loop gracefully.

**Parameters**:

- **session** (Session): The current session.

### method add_rail

```python
add_rail(rail: Rail) -> DeepAgent
```

Register an additional guardrail on the agent.

**Parameters**:

- **rail** (Rail): The rail instance to add.

**Returns**:

**DeepAgent**: `self`, for method chaining.

### method create_subagent

```python
create_subagent(
    subagent_type: str,
    subsession_id: str,
) -> DeepAgent
```

Create and return a configured sub-agent of the given type.

**Parameters**:

- **subagent_type** (str): The sub-agent type identifier (e.g. `"browser"`, `"code"`, `"research"`).
- **subsession_id** (str): A unique session ID for the sub-agent.

**Returns**:

**DeepAgent**: The newly created sub-agent instance.

### method load_state

```python
load_state(session: Session) -> DeepAgentState
```

Load the persisted agent state from the session.

**Parameters**:

- **session** (Session): The session to load from.

**Returns**:

**[DeepAgentState](./schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)**: The loaded state, or a fresh default state if none was persisted.

### method save_state

```python
save_state(
    session: Session,
    state: DeepAgentState,
) -> None
```

Persist the agent state into the session.

**Parameters**:

- **session** (Session): The session to save into.
- **state** ([DeepAgentState](./schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The state to persist.

### method clear_state

```python
clear_state(
    session: Session,
    clear_persisted: bool = True,
) -> None
```

Reset the in-memory agent state and optionally remove the persisted copy.

**Parameters**:

- **session** (Session): The session whose state to clear.
- **clear_persisted** (bool, optional): Whether to also remove the persisted state from the session store. Default: `True`.
