# openjiuwen.core.operator.llm_call.base

`openjiuwen.core.operator.llm_call.base` provides the **LLM prompt parameter handle** `LLMCallOperator` for self-evolution of LLM prompts. It manages `system_prompt` and `user_prompt` parameters, supports freeze markers to control tunability, and pushes parameter changes to the consumer via the `on_parameter_updated` callback.

---

## class openjiuwen.core.operator.llm_call.base.LLMCallOperator

LLM prompt parameter handle; tunable parameters are `system_prompt` and `user_prompt` (controlled by freeze markers). Changes are pushed to the consumer via the `on_parameter_updated` callback.

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

**Parameters**:

* **system_prompt** (str | List[Dict]): System prompt, supports string template or message list. Strings support `{{key}}` placeholders.
* **user_prompt** (str | List[Dict]): User prompt template (supports `{{query}}` placeholders).
* **freeze_system_prompt** (bool, optional): When `True`, `system_prompt` is not tunable. Default: `False`.
* **freeze_user_prompt** (bool, optional): When `True`, `user_prompt` is not tunable. Default: `True`.
* **operator_id** (str, optional): Unique operator identifier. Default: `"llm_call"`.
* **on_parameter_updated** (Callable, optional): Callback when parameters change, signature `(target: str, value: Any) -> None`. Default: `None`.

### operator_id -> str

Return the unique operator identifier.

### get_tunables() -> Dict[str, TunableSpec]

Return tunable parameter specs. Only includes unfrozen parameters (`system_prompt` and/or `user_prompt`). Parameter kind is `"prompt"`.

### set_parameter(target: str, value: Any) -> None

Set a tunable parameter value (evolution update). Only updates unfrozen parameters, then triggers the `on_parameter_updated` callback.

**Parameters**:

* **target** (str): Parameter name (`system_prompt` or `user_prompt`).
* **value** (Any): New prompt content (str or list).

### get_state() -> Dict[str, Any]

Get current prompt state for checkpointing.

**Returns**:

**Dict[str, Any]**, dictionary containing `system_prompt` and `user_prompt`.

### load_state(state: Dict[str, Any]) -> None

Restore prompt state from checkpoint. Does NOT check freeze markers (checkpoint recovery must restore full state). Updates fields one by one and triggers callbacks.

**Parameters**:

* **state** (Dict[str, Any]): State dictionary containing `system_prompt` and/or `user_prompt`.

### set_freeze_system_prompt(switch: bool) -> None

Set the system prompt freeze state.

**Parameters**:

* **switch** (bool): `True` to freeze (not tunable), `False` to enable tuning.

### set_freeze_user_prompt(switch: bool) -> None

Set the user prompt freeze state.

**Parameters**:

* **switch** (bool): `True` to freeze (not tunable), `False` to enable tuning.

### get_freeze_system_prompt() -> bool

Return whether the system prompt is frozen.

**Returns**:

**bool**, `True` means frozen (not tunable).

### get_freeze_user_prompt() -> bool

Return whether the user prompt is frozen.

**Returns**:

**bool**, `True` means frozen (not tunable).

---

## Alias

`LLMCall` is a backward-compatible alias for `LLMCallOperator`.
