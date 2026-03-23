# openjiuwen.dev_tools.agent_builder.executor

`openjiuwen.dev_tools.agent_builder.executor` handles **build execution and dialog history**:

- Maps `model_info` to framework [Model](../../../openjiuwen.core/foundation/llm/llm.md) and constructs `AgentBuilderExecutor` to drive [BaseAgentBuilder](builders.md);
- Provides `HistoryManager` / `HistoryCache` for multi-turn dialogs.

**Exports**: `AgentBuilderExecutor`, `HistoryManager`, `HistoryCache`.

---

## func openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model

Maps a caller `model_info` dict to [Model](../../../openjiuwen.core/foundation/llm/llm.md) (builds `ModelClientConfig` and `ModelRequestConfig`; see [llm.md](../../../openjiuwen.core/foundation/llm/llm.md)).

**Parameters**:

* **model_info**(Dict[str, Any], optional): Must include `model_provider` (or `client_provider`), `model_name` (or `model`), `api_key`; optional `api_base`, `temperature`, `max_tokens`, `top_p`, `timeout`, `verify_ssl`, etc. Default: `None` (treated as empty dict).

**Returns**:

**[Model](../../../openjiuwen.core/foundation/llm/llm.md)** with client and request configuration.

**Raises**:

* **ValidationError**: Missing required fields or invalid config (`COMPONENT_LLM_CONFIG_INVALID`).

---

## class openjiuwen.dev_tools.agent_builder.executor.executor.AgentBuilderExecutor

```python
class openjiuwen.dev_tools.agent_builder.executor.executor.AgentBuilderExecutor(
    query: str,
    session_id: str,
    agent_type: str,
    history_manager_map: Dict[str, HistoryManager],
    agent_builder_map: Optional[Dict[str, BaseAgentBuilder]] = None,
    model_info: Optional[Dict[str, Any]] = None,
    enable_progress: bool = True,
)
```

Single-run executor: creates or reuses `HistoryManager` and [BaseAgentBuilder](builders.md), appends the user message, then calls `agent_builder.execute(query)`.

**Parameters**:

* **query**(str): Current user message.
* **session_id**(str): Session id.
* **agent_type**(str): `'llm_agent'` or `'workflow'` (converted to [AgentType](utils.md)).
* **history_manager_map**(Dict[str, HistoryManager]): Shared history map (held by [AgentBuilder](agent_builder.md)).
* **agent_builder_map**(Dict[str, BaseAgentBuilder], optional): Per-session builder reuse. Default: `None`.
* **model_info**(Dict[str, Any], optional): Passed to `create_core_model`. Default: `None`.
* **enable_progress**(bool, optional): Whether to create a [ProgressReporter](utils.md). Default: `True`.

**Raises**:

* **ValidationError**: Unsupported `agent_type` or invalid model configuration.

### staticmethod get_history_manager(session_id: str, history_manager_map: Dict[str, HistoryManager]) -> HistoryManager

Creates a new `HistoryManager` for `session_id` when missing.

### execute() -> Any

Appends the user message and returns `agent_builder.execute(self.query)`.

### get_build_status() -> Dict[str, Any]

Adds `session_id` and `agent_type` to the builder `get_build_status()` result.

---

## class openjiuwen.dev_tools.agent_builder.executor.history_manager.DialogueMessage

Dataclass for one dialog message.

* **content**(str): Text content.
* **role**(str): Role (e.g. `user`, `assistant`).
* **timestamp**(datetime): Timestamp.

### to_dict() -> Dict[str, str]

Returns only `role` and `content`.

---

## class openjiuwen.dev_tools.agent_builder.executor.history_manager.HistoryCache

In-memory dialog cache backed by a list of `DialogueMessage`, with optional max length.

**Parameters**:

* **max_history_size**(int, optional): Max messages. Default: package constant `DEFAULT_MAX_HISTORY_SIZE`.

### get_history() -> List[DialogueMessage]

Copy of history.

### get_messages(num: int) -> List[Dict[str, Any]]

Returns the last `num` messages as dicts; `num <= 0` uses `max_history_size`.

### add_message(message: DialogueMessage) -> None

Appends a message; drops oldest when over capacity.

### clear() -> None

Clears history.

---

## class openjiuwen.dev_tools.agent_builder.executor.history_manager.HistoryManager

Session-level history with `add_user_message` / `add_assistant_message` / `get_history`, etc.

**Parameters**:

* **max_history_size**(int, optional): Passed to internal `HistoryCache`. Default: see source.

### property dialogue_history -> HistoryCache

Underlying cache.

### get_latest_k_messages(k: int) -> List[Dict[str, Any]]

Last `k` messages as dicts.

### get_history() -> List[Dict[str, Any]]

All messages as dicts.

### add_message(content: str, role: str, timestamp: Optional[datetime] = None) -> None

Adds one message.

### add_assistant_message(content: str) -> None

Adds an assistant message.

### add_user_message(content: str) -> None

Adds a user message.

### clear() -> None

Clears session history.
