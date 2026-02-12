# openjiuwen.core.workflow.components.condition.number

The `openjiuwen.core.workflow.components.condition.number` module provides number-based loop condition implementation, used in [LoopComponent] to loop a fixed number of times or according to a count limit in input. Based on comparison between current round index `INDEX` in `session.state()` and limit `limit`, determines whether to continue looping. Used in conjunction with [loop_comp](../flow/loop/loop_comp.md). Condition classes are exported through `openjiuwen.core.workflow`, it is recommended to use `from openjiuwen.core.workflow import NumberCondition` for import.

## class openjiuwen.core.workflow.components.condition.number.NumberCondition

```python
class openjiuwen.core.workflow.components.condition.number.NumberCondition(limit: Union[str, int])
```

Number-based loop condition: based on comparison between `limit` (or limit parsed from input) and `INDEX` in `session.state()`, when `current_idx < limit_num` condition is true, continue looping; otherwise end. Inherits from [Condition](./condition.md).

**Parameters**:

- **limit** (str | int): Loop count limit. Can be an integer, or a key (str) pointing to input, parsed as integer by upper layer during evaluation.

### invoke(inputs: Input, session: BaseSession) -> Output

Evaluate whether to continue looping in the current round: read `INDEX` from `session.state()`, compare with effective count limit this round, return `current_idx < limit_num`.

**Parameters**:

- **inputs** (Input): Current input, used to parse limit value when `limit` is str.
- **session** (BaseSession): Session, used to read `INDEX`.

**Returns**:

- **Output**: bool, `True` means continue looping, `False` means end.

**Exceptions**:

- **BaseError**: When `limit` is parsed as `None` or illegal value, error code see `NUMBER_CONDITION_ERROR` in [StatusCode](../../../common/exception/status_code.md).

---

## class openjiuwen.core.workflow.components.condition.number.NumberConditionInSession

```python
class openjiuwen.core.workflow.components.condition.number.NumberConditionInSession(limit: int)
```

Internal implementation class `NumberConditionInSession` is similar to `NumberCondition`, suitable for scenarios where loop count is already determined in configuration or session. If `limit` is `None`, will throw `NUMBER_CONDITION_ERROR` during `invoke`. Loop count is also limited by maximum loop count in session environment variables (e.g., `LOOP_NUMBER_MAX_LIMIT_KEY`), when exceeded, exceptions are thrown by upper layers like [LoopComponent].

**Parameters**:

- **limit** (int): Loop count limit, is an integer, does not depend on input parsing.
