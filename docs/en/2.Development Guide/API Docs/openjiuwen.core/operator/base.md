# openjiuwen.core.operator.base

`openjiuwen.core.operator.base` provides the **parameter handle** abstraction required for self-evolution: Operators support trajectory attribution via `operator_id`, and parameter optimization via `get_tunables`, `set_parameter`, `get_state`/`load_state`. The operator itself does not execute; it only manages parameters. Execution is performed by the consumer (Agent).

---

## class openjiuwen.core.operator.base.TunableSpec

Describes a single tunable parameter of an operator.

* **name** (str): Parameter name.
* **kind** (TunableKind): Tunable type (e.g., prompt, continuous, discrete, text).
* **path** (str): Path of the parameter within the operator.
* **constraint** (Any, optional): Constraint (e.g., range, enumeration).

```text
class TunableSpec(name: str, kind: TunableKind, path: str, constraint: Optional[Any] = None)
```

---

## class openjiuwen.core.operator.base.Operator

Abstract base class for self-evolution parameter handles. Operators provide a unified interface for the evolution framework:
- Identifies the operator via `operator_id` (for trajectory attribution)
- Describes tunable parameters via `get_tunables`
- Reads current values via `get_state`
- Updates parameters via `set_parameter` (checks frozen flag)
- Restores from checkpoints via `load_state` (does not check frozen flag)

Parameter changes are pushed to consumers (Agent/Rail) via the `on_parameter_updated` callback, ensuring immediate synchronization.

```text
class Operator()
```

### @property abstractmethod operator_id() -> str

The unique identifier for the operator used in traces and attribution.

**Returns**:

**str**, the operator ID.

### abstractmethod get_tunables() -> Dict[str, TunableSpec]

Describes tunable parameters and their constraints. Frozen parameters should not be included in the return value.

**Returns**:

**Dict[str, TunableSpec]**, mapping from parameter name to specification.

### abstractmethod get_state() -> Dict[str, Any]

Gets current parameter values for checkpointing/rollback.

**Returns**:

**Dict[str, Any]**, a serializable state dictionary.

### abstractmethod set_parameter(target: str, value: Any) -> None

Sets a parameter value (evolution update). Constraints: 1) Checks whether the target parameter is frozen (skips if frozen); 2) Updates internal state; 3) Triggers the `on_parameter_updated` callback to synchronize the consumer. This is the sole entry point for evolution updates.

**Parameters**:

* **target** (str): Parameter name.
* **value** (Any): New value.

### abstractmethod load_state(state: Dict[str, Any]) -> None

Restores state from a checkpoint. Constraints: 1) Does not check frozen flag (must restore complete state); 2) Updates internal state field by field; 3) Triggers the `on_parameter_updated` callback for each field. This is the sole entry point for checkpoint restoration.

**Parameters**:

* **state** (Dict[str, Any]): State dictionary.
