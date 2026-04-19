# openjiuwen.core.operator.memory_call.base

`openjiuwen.core.operator.memory_call.base` 提供**记忆调用参数句柄** MemoryCallOperator，用于记忆调用参数的自演进。可调参数为 `enabled` 与 `max_retries`，参数变更通过 `on_parameter_updated` 回调推送给使用者。

---

## class openjiuwen.core.operator.memory_call.base.MemoryCallOperator

记忆调用参数句柄；可调参数为 `enabled`、`max_retries`。执行前在 session 上设置 operator_id 便于追踪；get_state/load_state 用于检查点与恢复。

```text
class MemoryCallOperator(
    operator_id: str = "memory_call",
    *,
    on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
)
```

**参数**：

* **operator_id**(str，可选)：算子唯一标识。默认值：`"memory_call"`。
* **on_parameter_updated**(Callable，可选)：参数变更时的回调函数，签名为 `(target: str, value: Any) -> None`。默认值：`None`。

### operator_id -> str

返回 `operator_id`。

### get_tunables() -> Dict[str, TunableSpec]

返回 enabled（bool）、max_retries（int，范围 [0, 5]）两个可调参数。

### set_parameter(target: str, value: Any) -> None

target 为 `enabled` 时设为 bool；为 `max_retries` 时设为 int 并限制在 [0, 5]。更新后触发回调。

### get_state() -> Dict[str, Any]

返回 `{"enabled": self._enabled, "max_retries": self._max_retries}`。

### load_state(state: Dict[str, Any]) -> None

从 state 恢复 enabled、max_retries。逐字段更新并触发回调。
