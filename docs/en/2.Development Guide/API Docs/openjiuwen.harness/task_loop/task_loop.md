# openjiuwen.harness.task_loop

## class openjiuwen.harness.task_loop.TaskLoopEventHandler

Handles events during each round of the task loop. Subclass or replace to customize agent behavior at key lifecycle points.

### async method prepare_round

```python
async prepare_round(session: Session, state: DeepAgentState) -> None
```

Called before each task-loop round. Use for pre-round setup such as refreshing context or updating the system prompt.

**Parameters**:

- **session** (Session): The current session.
- **state** ([DeepAgentState](../schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The current agent state.

### async method wait_completion

```python
async wait_completion(session: Session) -> dict
```

Wait for the inner `ReActAgent` to finish a single round and return its result.

**Parameters**:

- **session** (Session): The current session.

**Returns**:

**dict**: The round result from the inner agent.

### async method handle_input

```python
async handle_input(
    inputs: dict,
    session: Session,
    state: DeepAgentState,
) -> dict
```

Process the initial user input before the first round begins.

**Parameters**:

- **inputs** (dict): The user inputs.
- **session** (Session): The current session.
- **state** ([DeepAgentState](../schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The current agent state.

**Returns**:

**dict**: Potentially transformed inputs.

### async method handle_task_interaction

```python
async handle_task_interaction(
    result: dict,
    session: Session,
    state: DeepAgentState,
) -> None
```

Called after each round to process the result and decide whether to continue.

**Parameters**:

- **result** (dict): The round result.
- **session** (Session): The current session.
- **state** ([DeepAgentState](../schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The current agent state.

### async method handle_task_completion

```python
async handle_task_completion(
    result: dict,
    session: Session,
    state: DeepAgentState,
) -> dict
```

Called when the task loop finishes normally. Produces the final output.

**Parameters**:

- **result** (dict): The last round result.
- **session** (Session): The current session.
- **state** ([DeepAgentState](../schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The final agent state.

**Returns**:

**dict**: The final result dictionary.

### async method handle_task_failed

```python
async handle_task_failed(
    error: Exception,
    session: Session,
    state: DeepAgentState,
) -> dict
```

Called when the task loop encounters an unrecoverable error.

**Parameters**:

- **error** (Exception): The exception that caused the failure.
- **session** (Session): The current session.
- **state** ([DeepAgentState](../schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The agent state at failure time.

**Returns**:

**dict**: A result dictionary describing the failure.

### async method handle_follow_up

```python
async handle_follow_up(
    event: DeepLoopEvent,
    session: Session,
    state: DeepAgentState,
) -> None
```

Process a follow-up event injected into the loop.

**Parameters**:

- **event** ([DeepLoopEvent](../schema/loop_event.md#class-openjiuwenharnessschemadeeplopevent)): The follow-up event.
- **session** (Session): The current session.
- **state** ([DeepAgentState](../schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The current agent state.

### async method on_abort

```python
async on_abort(session: Session, state: DeepAgentState) -> None
```

Called when an abort event is received. Performs cleanup before stopping.

**Parameters**:

- **session** (Session): The current session.
- **state** ([DeepAgentState](../schema/state.md#class-openjiuwenharnessschemastate-deepagentstate)): The current agent state.

---

## class openjiuwen.harness.task_loop.TaskLoopEventExecutor

Executes tool-calling abilities within the task loop. Wraps the inner agent's ability execution with loop-aware bookkeeping.

### async method execute_ability

```python
async execute_ability(
    ability_name: str,
    inputs: dict,
    session: Session,
) -> dict
```

Execute a single ability (tool call) within the loop context.

**Parameters**:

- **ability_name** (str): The ability to execute.
- **inputs** (dict): Inputs for the ability.
- **session** (Session): The current session.

**Returns**:

**dict**: The ability result.

### async method cancel

```python
async cancel() -> None
```

Cancel any in-flight ability execution.

### classmethod build_deep_executor

```python
@classmethod
build_deep_executor(
    agent: DeepAgent,
    **kwargs,
) -> TaskLoopEventExecutor
```

Factory method that constructs a `TaskLoopEventExecutor` bound to the given `DeepAgent`.

**Parameters**:

- **agent** ([DeepAgent](../deep_agent.md#class-openjiuwenharnessdeepagent)): The parent agent.
- ****kwargs**: Additional configuration.

**Returns**:

**TaskLoopEventExecutor**: The constructed executor.

---

## class openjiuwen.harness.task_loop.LoopCoordinator

Tracks iteration count, token usage, abort requests, and stop-condition state for the task loop.

### method reset

```python
reset() -> None
```

Reset all tracked state (iteration counter, token usage, abort flag).

### method increment_iteration

```python
increment_iteration() -> int
```

Increment and return the current iteration count.

**Returns**:

**int**: The new iteration count.

### method add_token_usage

```python
add_token_usage(usage: dict) -> None
```

Accumulate token usage from a round.

**Parameters**:

- **usage** (dict): Token usage dictionary (e.g. `{"prompt_tokens": 100, "completion_tokens": 50}`).

### method set_last_result

```python
set_last_result(result: dict) -> None
```

Store the result of the most recent round for inspection by stop conditions.

**Parameters**:

- **result** (dict): The round result.

### method request_abort

```python
request_abort() -> None
```

Signal that the loop should abort at the next opportunity.

### method should_continue

```python
should_continue() -> bool
```

Evaluate stop conditions and return whether the loop should continue.

**Returns**:

**bool**: `True` if the loop should run another round, `False` otherwise.

### method get_state

```python
get_state() -> dict
```

Return the coordinator's current state as a dictionary.

**Returns**:

**dict**: State dictionary including iteration count, token usage, and abort status.

### method load_state

```python
load_state(state: dict) -> None
```

Restore coordinator state from a dictionary (e.g. after session resume).

**Parameters**:

- **state** (dict): Previously saved state dictionary.

---

## class openjiuwen.harness.task_loop.TaskLoopController

High-level controller that drives the task loop by submitting rounds and managing follow-up draining.

### async method submit_round

```python
async submit_round(
    inputs: dict,
    session: Session,
) -> None
```

Submit inputs for the next round of the task loop.

**Parameters**:

- **inputs** (dict): The round inputs.
- **session** (Session): The current session.

### async method wait_round_completion

```python
async wait_round_completion(session: Session) -> dict
```

Block until the current round finishes and return its result.

**Parameters**:

- **session** (Session): The current session.

**Returns**:

**dict**: The round result.

### async method drain_follow_up

```python
async drain_follow_up(session: Session) -> list[DeepLoopEvent]
```

Drain and return all pending follow-up events.

**Parameters**:

- **session** (Session): The current session.

**Returns**:

**list[[DeepLoopEvent](../schema/loop_event.md#class-openjiuwenharnessschemadeeplopevent)]**: The drained follow-up events.

### method has_follow_up

```python
has_follow_up() -> bool
```

Check whether there are pending follow-up events.

**Returns**:

**bool**: `True` if follow-ups are queued.
