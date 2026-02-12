# openjiuwen.core.workflow

`openjiuwen.core.workflow.components.condition.array` 模块提供基于数组的循环条件实现，用于在 [LoopComponent] 中按数组（或输入中的数组引用）逐元素迭代，并在每轮将当前索引对应的值写入会话状态。与 [loop_comp](../flow/loop/loop_comp.md) 配合使用。条件类通过 `openjiuwen.core.workflow` 导出，建议使用 `from openjiuwen.core.workflow import ArrayCondition` 导入。

## class openjiuwen.core.workflow.components.condition.array.ArrayCondition

```python
class openjiuwen.core.workflow.components.condition.array.ArrayCondition(arrays: dict[str, Union[str, list[Any]]])
```

基于数组的循环条件：根据 `arrays` 配置从输入或会话中按当前轮次索引取各数组的对应元素，更新 `session.state()`，并判断当前索引是否小于最短数组长度以决定是否继续循环。继承自 [Condition](condition.md)。

### invoke(inputs: Input, session: BaseSession) -> Output

求值当前轮次是否继续循环：从 `session.state()` 读取 `INDEX`，对 `arrays` 中每项从 `inputs` 取数组（或使用传入的 list），取最短长度，若当前索引超出则返回 `False`；否则将当前索引对应的各数组元素写入 `session.state()` 并返回 `True` 及本次更新的键值（供上层写回输出）。

**参数**：

- **inputs**（Input）：当前输入，用于解析 `arrays` 中值为 str 的数组引用。
- **session**（BaseSession）：会话，用于读写 `INDEX` 与状态。

**返回**：

- **Output**：`True` 表示继续循环，可带元组 `(True, io_updates)`；`False` 表示结束循环。

**异常**：

- **JiuWenBaseException**：当 `arrays` 中某项类型不符合或访问越界时，错误码参见 [StatusCode](../../../common/exception/status_code.md) 中的 `ARRAY_CONDITION_ERROR`。
