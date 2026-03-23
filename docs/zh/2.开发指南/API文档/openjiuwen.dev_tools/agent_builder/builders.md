# openjiuwen.dev_tools.agent_builder.builders

`openjiuwen.dev_tools.agent_builder.builders` 是 **Agent 构建器子包**，负责：

- 定义抽象基类 [BaseAgentBuilder](#class-openjiuwendevtoolsagent_builderbuildersbasebaseagentbuilder)，统一资源检索、状态机与 `execute` 模板流程；
- 通过 [AgentBuilderFactory](#class-openjiuwendevtoolsagent_builderbuildersfactoryagentbuilderfactory) 按 [AgentType](utils.md) 创建 [LlmAgentBuilder](builders/llm_agent.md) 或 [WorkflowBuilder](builders/workflow.md)；
- 具体 LLM Agent / 工作流逻辑分别见 [builders/llm_agent.md](builders/llm_agent.md) 与 [builders/workflow.md](builders/workflow.md)。

**包导出**：`BaseAgentBuilder`、`LlmAgentBuilder`、`WorkflowBuilder`、`AgentBuilderFactory`。

---

## class openjiuwen.dev_tools.agent_builder.builders.base.BaseAgentBuilder

```python
class openjiuwen.dev_tools.agent_builder.builders.base.BaseAgentBuilder(
    llm: Model,
    history_manager: HistoryManager,
    progress_reporter: Optional[ProgressReporter] = None,
)
```

Agent 构建器抽象基类，采用模板方法模式：`execute` 中依次完成资源更新，并按 [BuildState](utils.md) 分发到 `_handle_initial`、`_handle_processing`、`_handle_completed`。

子类需实现 `_handle_*`、`_reset_internal_state`、`_is_workflow_builder`。

**参数**：

* **llm**([Model](../../../openjiuwen.core/foundation/llm/llm.md))：用于检索与生成的大模型实例。
* **history_manager**([HistoryManager](executor.md))：会话历史管理器。
* **progress_reporter**([ProgressReporter](utils.md)，可选)：构建进度上报。默认值：`None`。

### property state -> BuildState

当前构建状态。

### property resource -> Dict[str, Any]

当前累积的资源字典（插件、工作流等，由检索器写入）。

### execute(query: str) -> Union[str, Dict[str, Any]]

执行一轮构建流程。可能返回中间字符串（澄清、Mermaid 等）或最终 DSL 字典。

**异常**：

* **ApplicationError**：未知 [BuildState](utils.md) 等状态错误。

### reset() -> None

重置为初始状态并清空资源，调用 `_reset_internal_state()`。

### get_build_status() -> Dict[str, Any]

返回包含 `state`、`resource_count` 等信息的字典。

### abstractmethod _handle_initial(query: str, dialog_history: List[Dict[str, str]]) -> Union[str, Dict[str, Any]]

初始状态处理（子类实现）。

### abstractmethod _handle_processing(query: str, dialog_history: List[Dict[str, str]]) -> Union[str, Dict[str, Any]]

处理中状态（子类实现）。

### abstractmethod _handle_completed(query: str, dialog_history: List[Dict[str, str]]) -> Union[str, Dict[str, Any]]

已完成状态（子类实现）。

### abstractmethod _reset_internal_state() -> None

子类内部字段重置。

### abstractmethod _is_workflow_builder() -> bool

是否为工作流构建器。

### is_workflow_builder() -> bool

返回 `_is_workflow_builder()` 的结果。

---

## class openjiuwen.dev_tools.agent_builder.builders.factory.AgentBuilderFactory

Agent 构建器工厂。首次 `create` 时懒加载注册 `LlmAgentBuilder` 与 `WorkflowBuilder`。

### classmethod create(agent_type: AgentType, llm: Model, history_manager: HistoryManager) -> BaseAgentBuilder

创建对应类型的构建器实例。

**参数**：

* **agent_type**([AgentType](utils.md))：如 `AgentType.LLM_AGENT`、`AgentType.WORKFLOW`。
* **llm**([Model](../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。
* **history_manager**([HistoryManager](executor.md))：历史管理器。

**异常**：

* **ValueError**：不支持的 `agent_type`。

### classmethod register(agent_type: AgentType, builder_class: Type[BaseAgentBuilder]) -> None

注册自定义构建器类型。

**异常**：

* **TypeError**：`builder_class` 未继承 `BaseAgentBuilder`。

### classmethod get_supported_types() -> list[AgentType]

返回已注册的 [AgentType](utils.md) 列表。

### classmethod clear_registry() -> None

清空注册表（多用于测试）。

### classmethod get_registered_builders() -> Dict[AgentType, Type[BaseAgentBuilder]]

返回注册表的浅拷贝。

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder.LlmAgentBuilder

详见 [builders/llm_agent.md](builders/llm_agent.md)。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.builder.WorkflowBuilder

详见 [builders/workflow.md](builders/workflow.md)。
