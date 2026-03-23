# openjiuwen.dev_tools.agent_builder.executor

`openjiuwen.dev_tools.agent_builder.executor` 是 **构建执行与会话历史子包**，负责：

- 将 `model_info` 映射为框架 [Model](../../../openjiuwen.core/foundation/llm/llm.md)，并创建 [AgentBuilderExecutor](#class-openjiuwendevtoolsagent_builderexecutorexecutoragentbuilderexecutor) 驱动 [BaseAgentBuilder](builders.md)；
- 提供 [HistoryManager](#class-openjiuwendevtoolsagent_builderexecutorhistory_managerhistorymanager) / [HistoryCache](#class-openjiuwendevtoolsagent_builderexecutorhistory_managerhistorycache) 管理多轮对话。

**包导出**：`AgentBuilderExecutor`、`HistoryManager`、`HistoryCache`。

---

## func openjiuwen.dev_tools.agent_builder.executor.executor.create_core_model

将业务侧传入的 `model_info` 字典转换为 [Model](../../../openjiuwen.core/foundation/llm/llm.md)（内部构造 `ModelClientConfig` 与 `ModelRequestConfig`，详见 [llm.md](../../../openjiuwen.core/foundation/llm/llm.md)）。

**参数**：

* **model_info**(Dict[str, Any]，可选)：需包含 `model_provider`（或 `client_provider`）、`model_name`（或 `model`）、`api_key`；可选 `api_base`、`temperature`、`max_tokens`、`top_p`、`timeout`、`verify_ssl` 等。默认值：`None`（按空字典处理）。

**返回**：

**[Model](../../../openjiuwen.core/foundation/llm/llm.md)**，已配置客户端与请求参数。

**异常**：

* **ValidationError**：缺少必填字段或配置无效（状态码 `COMPONENT_LLM_CONFIG_INVALID`）。

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

单次构建执行器：为会话创建或复用 [HistoryManager](#class-openjiuwendevtoolsagent_builderexecutorhistory_managerhistorymanager) 与 [BaseAgentBuilder](builders.md)，写入用户消息后调用 `agent_builder.execute(query)`。

**参数**：

* **query**(str)：本轮用户输入。
* **session_id**(str)：会话 ID。
* **agent_type**(str)：`'llm_agent'` 或 `'workflow'`（将转为 [AgentType](utils.md)）。
* **history_manager_map**(Dict[str, HistoryManager])：跨调用复用的历史表（由 [AgentBuilder](agent_builder.md) 持有）。
* **agent_builder_map**(Dict[str, BaseAgentBuilder]，可选)：按会话复用构建器。默认值：`None`。
* **model_info**(Dict[str, Any]，可选)：传入 `create_core_model`。默认值：`None`。
* **enable_progress**(bool，可选)：是否创建 [ProgressReporter](utils.md)。默认值：`True`。

**异常**：

* **ValidationError**：`agent_type` 不支持或模型配置无效。

### staticmethod get_history_manager(session_id: str, history_manager_map: Dict[str, HistoryManager]) -> HistoryManager

若不存在则为 `session_id` 新建 [HistoryManager](#class-openjiuwendevtoolsagent_builderexecutorhistory_managerhistorymanager) 并写入映射。

### execute() -> Any

追加用户消息后执行 `agent_builder.execute(self.query)`，返回构建结果。

### get_build_status() -> Dict[str, Any]

在构建器 `get_build_status()` 结果上附加 `session_id`、`agent_type`。

---

## class openjiuwen.dev_tools.agent_builder.executor.history_manager.DialogueMessage

数据类，表示一条对话消息。

* **content**(str)：文本内容。
* **role**(str)：角色（如 `user`、`assistant`）。
* **timestamp**(datetime)：时间戳。

### to_dict() -> Dict[str, str]

返回仅含 `role`、`content` 的字典。

---

## class openjiuwen.dev_tools.agent_builder.executor.history_manager.HistoryCache

对话缓存，底层为 `DialogueMessage` 列表，支持最大条数裁剪。

**参数**：

* **max_history_size**(int，可选)：最大条数，默认使用包内常量。默认值：见源码 `DEFAULT_MAX_HISTORY_SIZE`。

### get_history() -> List[DialogueMessage]

返回历史副本。

### get_messages(num: int) -> List[Dict[str, Any]]

取最近 `num` 条并转为字典列表；`num <= 0` 时使用 `max_history_size`。

### add_message(message: DialogueMessage) -> None

追加消息，超长时丢弃最早一条。

### clear() -> None

清空历史。

---

## class openjiuwen.dev_tools.agent_builder.executor.history_manager.HistoryManager

会话级历史管理，对外接口为 `add_user_message` / `add_assistant_message` / `get_history` 等。

**参数**：

* **max_history_size**(int，可选)：传递给内部 [HistoryCache](#class-openjiuwendevtoolsagent_builderexecutorhistory_managerhistorycache)。默认值：见源码。

### property dialogue_history -> HistoryCache

底层缓存对象。

### get_latest_k_messages(k: int) -> List[Dict[str, Any]]

最近 `k` 条消息字典。

### get_history() -> List[Dict[str, Any]]

全部消息（字典格式）。

### add_message(content: str, role: str, timestamp: Optional[datetime] = None) -> None

添加一条消息。

### add_assistant_message(content: str) -> None

添加助手消息。

### add_user_message(content: str) -> None

添加用户消息。

### clear() -> None

清空会话历史。
