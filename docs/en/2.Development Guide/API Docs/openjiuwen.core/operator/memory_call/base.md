# openjiuwen.core.operator.memory_call.base

`openjiuwen.core.operator.memory_call.base` provides the **memory call parameter handle** MemoryCallOperator for self-evolution of memory call parameters. The tunable parameters are `enabled` and `max_retries`. Parameter changes are pushed to consumers via the `on_parameter_updated` callback.

---

## class openjiuwen.core.operator.memory_call.base.MemoryCallOperator

Memory call parameter handle; tunable parameters are `enabled` and `max_retries`. Sets the operator_id on the session before execution for tracking; get_state/load_state are used for checkpointing and restoration.

```text
class MemoryCallOperator(
    operator_id: str = "memory_call",
    *,
    on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
)
```

**Parameters**:

* **operator_id** (str, optional): Unique identifier for the operator. Default: `"memory_call"`.
* **on_parameter_updated** (Callable, optional): Callback function invoked when a parameter changes, with signature `(target: str, value: Any) -> None`. Default: `None`.

### operator_id -> str

Returns the `operator_id`.

### get_tunables() -> Dict[str, TunableSpec]

Returns two tunable parameters: enabled (bool) and max_retries (int, range [0, 5]).

### set_parameter(target: str, value: Any) -> None

When target is `enabled`, sets the value as bool; when target is `max_retries`, sets the value as int clamped to [0, 5]. Triggers the callback after update.

### get_state() -> Dict[str, Any]

Returns `{"enabled": self._enabled, "max_retries": self._max_retries}`.

### load_state(state: Dict[str, Any]) -> None

Restores enabled and max_retries from state. Updates field by field and triggers the callback.
