# openjiuwen.core.operator.memory_call.base

`openjiuwen.core.operator.memory_call.base` 提供**记忆调用算子** MemoryCallOperator，将记忆读写/检索接入算子体系：支持通过 session.tracer() 追踪、通过 get_state/load_state 做检查点；可调参数为 enabled 与 max_retries。

---

## class openjiuwen.core.operator.memory_call.base.MemoryCallOperator

记忆调用算子；可调参数为 enabled、max_retries。执行前在 session 上设置 operator_id 便于追踪；get_state/load_state 用于检查点与恢复。

```text
class MemoryCallOperator(
    memory: Any = None,
    memory_call_id: str = "memory_call",
    *,
    memory_invoke: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None,
)
```

**参数**：

* **memory**(Any，可选)：记忆实例，用于执行。默认值：`None`。
* **memory_call_id**(str，可选)：算子唯一标识。默认值：`"memory_call"`。
* **memory_invoke**(Callable，可选)：自定义调用回调，用于非标准接口（如 LongTermMemory.search_user_mem）。默认值：`None`。

### operator_id -> str

返回 `memory_call_id`。

### get_tunables() -> Dict[str, TunableSpec]

返回 enabled（bool）、max_retries（int，范围 [0, 5]）两个可调参数。

### set_parameter(target: str, value: Any) -> None

target 为 `enabled` 时设为 bool；为 `max_retries` 时设为 int 并限制在 [0, 5]。

### get_state() -> Dict[str, Any]

返回 `{"enabled": self._enabled, "max_retries": self._max_retries}`。

### load_state(state: Dict[str, Any]) -> None

从 state 恢复 enabled、max_retries。

### async invoke(inputs: Dict[str, Any], session: Session, **kwargs) -> Any

执行记忆调用。支持两种模式：1）memory_invoke(inputs) 回调；2）memory.invoke(inputs, **kwargs)。禁用或未配置 memory/memory_invoke 时抛出 RuntimeError。

### async stream(inputs, session, **kwargs) -> AsyncIterator[Any]

若 memory 支持 stream，则委托并设置算子上下文；否则抛出 NotImplementedError。
