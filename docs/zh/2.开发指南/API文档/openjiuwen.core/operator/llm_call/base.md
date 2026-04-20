# openjiuwen.core.operator.llm_call.base

`openjiuwen.core.operator.llm_call.base` 提供**LLM 提示词参数句柄** LLMCallOperator，用于 LLM 提示词的自演进。它管理 `system_prompt` 和 `user_prompt` 参数，支持冻结标记控制可调性，参数变更通过 `on_parameter_updated` 回调推送给使用者。

---

## class openjiuwen.core.operator.llm_call.base.LLMCallOperator

LLM 提示词参数句柄；可调参数为 `system_prompt` 和 `user_prompt`（受冻结标记控制）。通过 `on_parameter_updated` 回调将变更推送给使用者。

```text
class LLMCallOperator(
    system_prompt: str | List[Dict],
    user_prompt: str | List[Dict],
    freeze_system_prompt: bool = False,
    freeze_user_prompt: bool = True,
    operator_id: str = "llm_call",
    *,
    on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
)
```

**参数**：

* **system_prompt**(str | List[Dict])：系统提示词，支持字符串模板或消息列表。字符串支持 `{{key}}` 占位符。
* **user_prompt**(str | List[Dict])：用户提示词模板（支持 `{{query}}` 等占位符）。
* **freeze_system_prompt**(bool，可选)：为 `True` 时 `system_prompt` 不可调。默认值：`False`。
* **freeze_user_prompt**(bool，可选)：为 `True` 时 `user_prompt` 不可调。默认值：`True`。
* **operator_id**(str，可选)：算子唯一标识。默认值：`"llm_call"`。
* **on_parameter_updated**(Callable，可选)：参数变更时的回调函数，签名为 `(target: str, value: Any) -> None`。默认值：`None`。

### operator_id -> str

返回算子唯一标识。

### get_tunables() -> Dict[str, TunableSpec]

返回可调参数规格。仅包含未冻结的参数（`system_prompt` 和/或 `user_prompt`）。参数 kind 为 `"prompt"`。

### set_parameter(target: str, value: Any) -> None

设置可调参数值（进化更新）。仅更新未冻结的参数，更新后触发 `on_parameter_updated` 回调。

**参数**：

* **target**(str)：参数名（`system_prompt` 或 `user_prompt`）。
* **value**(Any)：新的提示词内容（str 或 list）。

### get_state() -> Dict[str, Any]

获取当前提示词状态，用于检查点。

**返回**：

**Dict[str, Any]**，包含 `system_prompt` 和 `user_prompt` 的字典。

### load_state(state: Dict[str, Any]) -> None

从检查点恢复提示词状态。不检查冻结标记（检查点恢复必须恢复完整状态）。逐字段更新并触发回调。

**参数**：

* **state**(Dict[str, Any])：包含 `system_prompt` 和/或 `user_prompt` 的状态字典。

### set_freeze_system_prompt(switch: bool) -> None

设置系统提示词冻结状态。

**参数**：

* **switch**(bool)：`True` 冻结（不可调），`False` 启用调优。

### set_freeze_user_prompt(switch: bool) -> None

设置用户提示词冻结状态。

**参数**：

* **switch**(bool)：`True` 冻结（不可调），`False` 启用调优。

### get_freeze_system_prompt() -> bool

返回系统提示词是否已冻结。

**返回**：

**bool**，`True` 表示已冻结（不可调）。

### get_freeze_user_prompt() -> bool

返回用户提示词是否已冻结。

**返回**：

**bool**，`True` 表示已冻结（不可调）。

---

## 别名

`LLMCall` 是 `LLMCallOperator` 的向后兼容别名。
