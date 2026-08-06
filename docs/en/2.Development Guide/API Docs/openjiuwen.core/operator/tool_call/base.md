# openjiuwen.core.operator.tool_call.base

`openjiuwen.core.operator.tool_call.base` provides the **tool description parameter handle** ToolCallOperator for self-evolution of tool descriptions. It manages the `tool_description` parameter (a mapping from tool names to description text). Parameter changes are pushed to consumers via the `on_parameter_updated` callback.

---

## class openjiuwen.core.operator.tool_call.base.ToolCallOperator

Tool description parameter handle; the tunable parameter is `tool_description`. Exposes the `tool_description` tunable parameter when `descriptions` is set, for tool description self-evolution.

```text
class ToolCallOperator(
    operator_id: str = "tool_call",
    descriptions: Optional[Dict[str, str]] = None,
    *,
    on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
)
```

**Parameters**:

* **operator_id** (str, optional): Unique identifier for the operator. Default: `"tool_call"`.
* **descriptions** (Dict[str, str], optional): Initial mapping from tool names to description text. When set, exposes the `tool_description` tunable parameter. Default: `None`.
* **on_parameter_updated** (Callable, optional): Callback function invoked when a parameter changes, with signature `(target: str, value: Any) -> None`. Default: `None`.

### operator_id -> str

Returns the unique operator identifier.

### get_tunables() -> Dict[str, TunableSpec]

Returns a dictionary containing `tool_description` when `descriptions` is non-empty; otherwise returns an empty dictionary. The kind of `tool_description` is `"text"`, with constraint `{"type": "dict"}`.

### set_parameter(target: str, value: Any) -> None

Only handles the case where target is `"tool_description"`. Value is Dict[tool_name, description_str], updates the internal cache and triggers the callback.

### get_state() -> Dict[str, Any]

Returns `{"tool_description": self._descriptions}`, a copy of the current tool description mapping.

### load_state(state: Dict[str, Any]) -> None

Restores `tool_description` from state. Updates field by field and triggers the callback.
