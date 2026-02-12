# openjiuwen.core.operator.tool_call.base

`openjiuwen.core.operator.tool_call.base` 提供**工具调用算子** ToolCallOperator，将工具调用接入算子体系：支持通过 session.tracer() 追踪、通过 get_state/load_state 做检查点；在设置 tool_registry 时暴露 tool_description 可调参数，用于工具描述的自演进。

---

## class openjiuwen.core.operator.tool_call.base.ToolCallOperator

工具调用算子；可调参数目前仅支持工具描述（tool_description）。设置 tool_registry 时暴露 tool_description，用于工具描述自演进；执行前在 session 上设置 operator_id 便于追踪。

```text
class ToolCallOperator(
    tool: Any = None,
    tool_call_id: str = "tool_call",
    *,
    tool_executor: Optional[Callable[[Any, Session], Awaitable[Tuple[Any, Any]]]] = None,
    tool_registry: Optional[Any] = None,
)
```

**参数**：

* **tool**(Any，可选)：工具实例，用于执行。默认值：`None`。
* **tool_call_id**(str，可选)：算子唯一标识。默认值：`"tool_call"`。
* **tool_executor**(Callable，可选)：对 inputs 中 tool_calls 列表的自定义执行器（路由模式）。默认值：`None`。
* **tool_registry**(Any，可选)：提供 get_tool_defs()、get_tools()、set_tool_description(tool_name, description) 的对象；设置后暴露 tool_description 可调参数。默认值：`None`。

### operator_id -> str

返回 `tool_call_id`。

### get_tunables() -> Dict[str, TunableSpec]

当 tool_registry 存在时返回仅包含 `tool_description` 的字典；set_parameter('tool_description', value) 的 value 为 Dict[tool_name, description_str]。否则返回空字典。

### set_parameter(target: str, value: Any) -> None

仅处理 target 为 `tool_description`；value 为 Dict[tool_name, description_str]，通过 tool_registry.set_tool_description 写入。

### get_state() -> Dict[str, Any]

返回 `{"enabled": self._enabled, "max_retries": self._max_retries}`。

### load_state(state: Dict[str, Any]) -> None

从 state 恢复 enabled、max_retries（max_retries 限制在 [0, 5]）。

### async invoke(inputs: Dict[str, Any], session: Session, **kwargs) -> Any

执行工具调用。支持两种模式：1）路由模式：inputs["tool_calls"] 为列表时使用 tool_executor(tool_call, session)；2）直连模式：使用 self._tool.invoke(inputs, **kwargs)。禁用或未配置 tool/tool_executor 时抛出 RuntimeError。

### async stream(inputs, session, **kwargs) -> AsyncIterator[Any]

若 _tool 支持 stream，则委托并设置算子上下文；否则抛出 NotImplementedError。
