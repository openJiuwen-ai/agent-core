# openjiuwen.harness.schema.loop_event

## enum openjiuwen.harness.schema.DeepLoopEventType

```python
class DeepLoopEventType(str, Enum)
```

Types of events that can be injected into the task loop.

| Value | Description |
|---|---|
| `FOLLOWUP` | A follow-up message from the user or another agent. |
| `STEER` | A steering instruction that redirects the agent mid-run. |
| `ABORT` | A request to abort the current task loop. |

---

## class openjiuwen.harness.schema.DeepLoopEvent

A single event in the task-loop priority queue.

**Attributes**:

- **priority** (int): Numeric priority (lower = higher priority).
- **seq** (int): Sequence number for stable ordering among events with equal priority.
- **created_at** (float): Timestamp when the event was created (`time.time()`).
- **event_id** (str): Unique event identifier (UUID).
- **event_type** ([DeepLoopEventType](#enum-openjiuwenharnessschemadeeplopeventtype)): The type of this event.
- **content** (str): The message or instruction content.
- **task_id** (str, optional): Target task ID, if this event is scoped to a specific task. Default: `None`.
- **metadata** (dict, optional): Additional metadata. Default: `{}`.

---

## function openjiuwen.harness.schema.create_loop_event

```python
create_loop_event(
    event_type: DeepLoopEventType,
    content: str,
    task_id: str | None = None,
    metadata: dict | None = None,
) -> DeepLoopEvent
```

Helper that creates a `DeepLoopEvent` with auto-generated `event_id`, `seq`, `created_at`, and the default priority for the given event type.

**Parameters**:

- **event_type** ([DeepLoopEventType](#enum-openjiuwenharnessschemadeeplopeventtype)): The event type.
- **content** (str): The message content.
- **task_id** (str, optional): Target task ID. Default: `None`.
- **metadata** (dict, optional): Additional metadata. Default: `None`.

**Returns**:

**[DeepLoopEvent](#class-openjiuwenharnessschemadeeplopevent)**: The constructed event.

---

## function openjiuwen.harness.schema.default_event_priority

```python
default_event_priority(event_type: DeepLoopEventType) -> int
```

Return the default numeric priority for a given event type.

| Event Type | Default Priority |
|---|---|
| `ABORT` | `0` (highest) |
| `STEER` | `10` |
| `FOLLOWUP` | `20` |

**Parameters**:

- **event_type** ([DeepLoopEventType](#enum-openjiuwenharnessschemadeeplopeventtype)): The event type.

**Returns**:

**int**: The default priority value.
