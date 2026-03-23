# openjiuwen.dev_tools.agent_builder

`openjiuwen.dev_tools.agent_builder` 是 openJiuwen 中的 **Agent 构建开发工具包**，负责：

- 提供统一入口 `AgentBuilder`（见下文），基于会话编排 LLM Agent 与工作流 Agent 的构建流程；
- 通过 [AgentBuilderExecutor](executor.md) 与 [BaseAgentBuilder](builders.md) 实现可复用的构建执行与状态管理；
- 与进度上报、历史记录、资源检索等子模块协同工作。

**子模块文档导航**：

- [builders](builders.md)：`BaseAgentBuilder`、`AgentBuilderFactory` 及各类 Builder 工厂。
- [builders/llm_agent](builders/llm_agent.md)：LLM Agent 澄清、生成与 DSL 转换。
- [builders/workflow](builders/workflow.md)：工作流意图、设计、DL 生成与校验。
- [builders/workflow/dl_transformer](builders/workflow/dl_transformer.md)：DL 与 Mermaid、工作流 DSL 互转。
- [builders/workflow/dl_transformer/converters](builders/workflow/dl_transformer/converters.md)：DL 节点到平台 DSL 的转换器。
- [builders/workflow/workflow_designer](builders/workflow/workflow_designer.md)：SE 工作流设计（基础设计、分支设计、反思评估）。
- [executor](executor.md)：执行器与 `create_core_model`。
- [resource](resource.md)：插件资源检索与预处理。
- [utils](utils.md)：枚举、进度、通用工具函数。

**包级导出**（`from openjiuwen.dev_tools.agent_builder import ...`）：

| 符号 | 说明 |
|------|------|
| `AgentBuilder` | 本文档 [AgentBuilder](#class-openjiuwendevtoolsagentbuildermainagentbuilder) |
| `AgentBuilderExecutor` | 见 [executor.md](executor.md) |
| `HistoryManager`、`HistoryCache` | 见 [executor.md](executor.md) |
| `BaseAgentBuilder`、`LlmAgentBuilder`、`WorkflowBuilder`、`AgentBuilderFactory` | 见 [builders.md](builders.md) 与子文档 |

---

## class openjiuwen.dev_tools.agent_builder.main.AgentBuilder

```python
class openjiuwen.dev_tools.agent_builder.main.AgentBuilder(
    model_info: Optional[Dict[str, Any]] = None,
    history_manager_map: Optional[Dict[str, HistoryManager]] = None,
    agent_builder_map: Optional[Dict[str, BaseAgentBuilder]] = None,
)
```

统一 Agent 构建入口类。在内部维护会话维度的 [HistoryManager](executor.md) 与 [BaseAgentBuilder](builders.md) 实例映射，封装对 [AgentBuilderExecutor](executor.md) 的调用与响应拼装。

**参数**：

* **model_info**(Dict[str, Any]，可选)：大模型配置，供执行器创建 [Model](../../../openjiuwen.core/foundation/llm/llm.md#class-openjiuwencorefoundationllmmodelmodel)。需包含 `model_provider`（或 `client_provider`）、`model_name`（或 `model`）、`api_key` 等字段。默认值：`{}`。
* **history_manager_map**(Dict[str, HistoryManager]，可选)：按 `session_id` 复用的会话历史管理器表。默认值：`None`（内部使用空字典）。
* **agent_builder_map**(Dict[str, BaseAgentBuilder]，可选)：按 `session_id` 复用的构建器实例表。默认值：`None`（内部使用空字典）。

### build_agent(query: str, session_id: str, agent_type: str = "llm_agent") -> Dict[str, Any]

统一构建接口。内部创建 [AgentBuilderExecutor](executor.md) 并执行 `execute()`，再根据返回类型与构建状态组装对外字典（可能包含 `dsl`、`response`、`mermaid_code`、`status` 等）。

**参数**：

* **query**(str)：用户输入或补充说明。
* **session_id**(str)：会话 ID，用于关联历史与 Builder。
* **agent_type**(str，可选)：`'llm_agent'` 或 `'workflow'`。默认值：`'llm_agent'`。

**返回**：

**Dict[str, Any]**，至少包含 `status`、`session_id`、`agent_type`；其余字段随构建阶段变化（例如澄清中为 `response`，完成为 `dsl` 等）。

### build_llm_agent(query: str, session_id: str) -> Dict[str, Any]

等价于 `build_agent(query, session_id, "llm_agent")`。

### build_workflow(query: str, session_id: str) -> Dict[str, Any]

等价于 `build_agent(query, session_id, "workflow")`。

### get_session_history(session_id: str, k: Optional[int] = None) -> List[Dict[str, str]]

获取指定会话的对话历史。

**参数**：

* **session_id**(str)：会话 ID。
* **k**(int，可选)：仅返回最近 `k` 条；为 `None` 时返回全部。默认值：`None`。

**返回**：

**List[Dict[str, str]]**，元素含 `role`、`content`。

### clear_session(session_id: str) -> None

清空会话历史，并对该会话上的 Builder 调用 `reset()`。

### get_build_status(session_id: str) -> Dict[str, Any]

从 `agent_builder_map` 取对应 Builder 的 `get_build_status()`；若会话不存在则返回 `state` 为 `not_found` 的字典。

### staticmethod get_progress(session_id: str) -> Optional[Dict[str, Any]]

从全局 [progress_manager](utils.md) 读取构建进度，存在则返回 `BuildProgress.to_dict()` 结果。

### staticmethod map_state_to_status(state: str, agent_type: str) -> str

将内部状态 `initial` / `processing` / `completed` 映射为对外 `status` 字符串（如 LLM Agent 初始为 `clarifying`，工作流初始为 `requesting`）。

**样例**：

```python
>>> from openjiuwen.dev_tools.agent_builder import AgentBuilder
>>>
>>> builder = AgentBuilder(
...     model_info={
...         "model_provider": "OpenAI",
...         "model_name": "your_model",
...         "api_key": "your_api_key",
...         "api_base": "https://api.example.com/v1",
...     }
... )
>>> result = builder.build_llm_agent(
...     query="创建一个客服助手",
...     session_id="session_demo_001",
... )
>>> isinstance(result.get("session_id"), str)
True
```
