# openjiuwen.core.operator.tool_call.base

`openjiuwen.core.operator.tool_call.base` 提供**工具描述参数句柄** ToolCallOperator，用于工具描述的自演进。它管理 `tool_description` 参数（工具名称到描述文本的映射），参数变更通过 `on_parameter_updated` 回调推送给使用者。

---

## class openjiuwen.core.operator.tool_call.base.ToolCallOperator

工具描述参数句柄；可调参数为 `tool_description`。在设置 `descriptions` 时暴露 `tool_description` 可调参数，用于工具描述自演进。

```text
class ToolCallOperator(
    operator_id: str = "tool_call",
    descriptions: Optional[Dict[str, str]] = None,
    *,
    on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
)
```

**参数**：

* **operator_id**(str，可选)：算子唯一标识。默认值：`"tool_call"`。
* **descriptions**(Dict[str, str]，可选)：工具名称到描述文本的初始映射。设置后暴露 `tool_description` 可调参数。默认值：`None`。
* **on_parameter_updated**(Callable，可选)：参数变更时的回调函数，签名为 `(target: str, value: Any) -> None`。默认值：`None`。

### operator_id -> str

返回算子唯一标识。

### get_tunables() -> Dict[str, TunableSpec]

当 `descriptions` 非空时返回包含 `tool_description` 的字典；否则返回空字典。`tool_description` 的 kind 为 `"text"`，constraint 为 `{"type": "dict"}`。

### set_parameter(target: str, value: Any) -> None

仅处理 target 为 `"tool_description"` 的情况。value 为 Dict[tool_name, description_str]，更新内部缓存并触发回调。

### get_state() -> Dict[str, Any]

返回 `{"tool_description": self._descriptions}`，即当前工具描述映射的副本。

### load_state(state: Dict[str, Any]) -> None

从 state 恢复 `tool_description`。逐字段更新并触发回调。
