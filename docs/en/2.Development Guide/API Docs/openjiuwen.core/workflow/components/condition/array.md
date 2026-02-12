# openjiuwen.core.workflow

The `openjiuwen.core.workflow.components.condition.array` module provides array-based loop condition implementation, used in [LoopComponent] to iterate element by element according to an array (or array reference in input), and write the value corresponding to the current index to session state in each round. Used in conjunction with [loop_comp](../flow/loop/loop_comp.md). Condition classes are exported through `openjiuwen.core.workflow`, it is recommended to use `from openjiuwen.core.workflow import ArrayCondition` for import.

## class openjiuwen.core.workflow.components.condition.array.ArrayCondition

```python
class openjiuwen.core.workflow.components.condition.array.ArrayCondition(arrays: dict[str, Union[str, list[Any]]])
```

Array-based loop condition: according to `arrays` configuration, get corresponding elements of each array from input or session by current round index, update `session.state()`, and determine whether current index is less than the shortest array length to decide whether to continue looping. Inherits from [Condition](condition.md).

### invoke(inputs: Input, session: BaseSession) -> Output

Evaluate whether to continue looping in the current round: read `INDEX` from `session.state()`, for each item in `arrays` get array from `inputs` (or use passed list), get shortest length, return `False` if current index exceeds; otherwise write current index corresponding array elements to `session.state()` and return `True` and this round's updated key-value pairs (for upper layer to write back to output).

**Parameters**:

- **inputs** (Input): Current input, used to parse array references with str values in `arrays`.
- **session** (BaseSession): Session, used to read and write `INDEX` and state.

**Returns**:

- **Output**: `True` means continue looping, can include tuple `(True, io_updates)`; `False` means end loop.

**Exceptions**:

- **JiuWenBaseException**: When an item in `arrays` has incorrect type or access is out of bounds, error code see `ARRAY_CONDITION_ERROR` in [StatusCode](../../../common/exception/status_code.md).
