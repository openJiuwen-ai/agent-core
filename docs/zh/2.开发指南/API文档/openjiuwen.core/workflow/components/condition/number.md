# openjiuwen.core.workflow.components.condition.number

`openjiuwen.core.workflow.components.condition.number` 模块提供基于次数的循环条件实现，用于在 [LoopComponent] 中按固定次数或输入中的次数上限循环。根据 `session.state()` 中的当前轮次索引 `INDEX` 与上限 `limit` 比较，判断是否继续循环。与 [loop_comp](../flow/loop/loop_comp.md) 配合使用。条件类通过 `openjiuwen.core.workflow` 导出，建议使用 `from openjiuwen.core.workflow import NumberCondition` 导入。

## class openjiuwen.core.workflow.components.condition.number.NumberCondition

```python
class openjiuwen.core.workflow.components.condition.number.NumberCondition(limit: Union[str, int])
```

基于次数的循环条件：根据 `limit`（或从输入中解析得到的上限）与 `session.state()` 中的 `INDEX` 比较，当 `current_idx < limit_num` 时条件为真，继续循环；否则结束。继承自 [Condition](condition.md)。

**参数**：

- **limit**（str | int）：循环次数上限。可为整数，或指向输入的 key（str），由上层在求值时解析为整数。

### invoke(inputs: Input, session: BaseSession) -> Output

求值当前轮次是否继续循环：从 `session.state()` 读取 `INDEX`，与本次有效的次数上限比较，返回 `current_idx < limit_num`。

**参数**：

- **inputs**（Input）：当前输入，当 `limit` 为 str 时用于解析上限值。
- **session**（BaseSession）：会话，用于读取 `INDEX`。

**返回**：

- **Output**：bool，`True` 表示继续循环，`False` 表示结束。

**异常**：

- **BaseError**：当 `limit` 解析为 `None` 或非法值时，错误码参见 [StatusCode](../../../common/exception/status_code.md) 中的 `NUMBER_CONDITION_ERROR`。

---

## class openjiuwen.core.workflow.components.condition.number.NumberConditionInSession

```python
class openjiuwen.core.workflow.components.condition.number.NumberConditionInSession(limit: int)
```

内部实现类 `NumberConditionInSession` 与 `NumberCondition` 类似，适用于循环次数在配置或会话中已确定的场景。若 `limit` 为 `None`，会在 `invoke` 时抛出 `NUMBER_CONDITION_ERROR`。循环次数还受会话环境变量中的最大循环次数限制（如 `LOOP_NUMBER_MAX_LIMIT_KEY`），超过时由 [LoopComponent]等上层抛出异常。

**参数**：

- **limit**（int）：循环次数上限，为整数，不依赖输入解析。
