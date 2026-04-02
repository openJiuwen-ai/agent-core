# openjiuwen.harness.schema.state

## class openjiuwen.harness.schema.DeepAgentState

Persistent state for a [`DeepAgent`](../deep_agent.md#class-openjiuwenharnessdeepagent) across task-loop iterations and session checkpoints.

**Attributes**:

- **iteration** (int): Current iteration count. Default: `0`.
- **task_plan** ([TaskPlan](./task.md#class-openjiuwenharnessschematasktaskplan), optional): The current task plan, if task planning is enabled. Default: `None`.
- **stop_condition_state** (dict, optional): Serialized stop-condition state for resumable loops. Default: `None`.
- **pending_follow_ups** (list[[DeepLoopEvent](./loop_event.md#class-openjiuwenharnessschemaloop_eventdeeplopevent)]): Follow-up events not yet consumed. Default: `[]`.

### method to_session_dict

```python
to_session_dict() -> dict
```

Serialize the state to a plain dictionary suitable for session persistence.

**Returns**:

**dict**: A JSON-serializable dictionary.

### classmethod from_session_dict

```python
@classmethod
from_session_dict(data: dict) -> DeepAgentState
```

Reconstruct a `DeepAgentState` from a dictionary previously produced by `to_session_dict()`.

**Parameters**:

- **data** (dict): The serialized state dictionary.

**Returns**:

**DeepAgentState**: The reconstructed state instance.
