# openjiuwen.dev_tools.agent_builder.builders.llm_agent

`openjiuwen.dev_tools.agent_builder.builders.llm_agent` 是 **LLM Agent 构建子模块**，负责：

- `LlmAgentBuilder`：需求澄清、配置生成、DSL 转换的完整流水线；
- `Clarifier`、`Generator`、`Transformer`：可单独复用的澄清、生成与转换组件；
- `IntentionDetector`：判断用户是否要优化已澄清需求。

**包导出**：`LlmAgentBuilder`、`Clarifier`、`Generator`、`Transformer`。

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder.LlmAgentBuilder

```python
class openjiuwen.dev_tools.agent_builder.builders.llm_agent.builder.LlmAgentBuilder(
    llm: Model,
    history_manager: HistoryManager,
)
```

继承 [BaseAgentBuilder](../builders.md)。`INITIAL` 阶段调用 [Clarifier](#class-openjiuwendevtoolsagent_builderbuildersllm_agentclarifierclarifier) 澄清并进入 `PROCESSING`；`PROCESSING` 阶段根据 [IntentionDetector](#class-openjiuwendevtoolsagent_builderbuildersllm_agentintention_detectorintentiondetector) 决定重新澄清或调用 [Generator](#class-openjiuwendevtoolsagent_builderbuildersllm_agentgeneratorgenerator) + [Transformer](#class-openjiuwendevtoolsagent_builderbuildersllm_agenttransformertransformer) 产出 DSL JSON 字符串，随后 `reset()`。

**参数**：

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。
* **history_manager**([HistoryManager](../executor.md))：会话历史。

### property agent_config_info -> Optional[str]

当前澄清得到的要素配置文本（内部使用）。

### property factor_output_info -> Optional[str]

澄清主输出（内部使用）。

### property display_resource_info -> Optional[str]

资源展示信息（内部使用）。

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.clarifier.Clarifier

需求澄清器：先抽取 Agent 要素，再结合可用资源做资源规划。

**参数**：

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。

### staticmethod parse_resource_output(resource_output: str, available_resources: Dict[Any, Any]) -> Tuple[str, Dict[str, List[str]]]

解析资源规划段落，返回展示文案与资源 ID 字典。

**异常**：

* **ApplicationError**：解析失败（`AGENT_BUILDER_RESOURCE_PARSE_ERROR`）。

### clarify(messages: str, resource: Dict[Any, Any]) -> Tuple[str, str, Dict[str, List[str]]]

执行两轮 LLM 调用并返回 `(要素输出, 资源展示文案, 资源 ID 字典)`。

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.generator.Generator

根据澄清结果生成结构化 Agent 配置字段，再合并资源 ID。

**参数**：

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。

### staticmethod parse_info(content: str) -> Dict[str, Any]

从模型输出中解析名称、描述、提示词、插件/知识库/工作流等字段。

### generate(message: str, agent_config_info: str, agent_resource_info: str, resource_id_dict: Dict[str, Any] = None) -> Dict[str, Any]

生成完整配置字典；若传入 `resource_id_dict` 会覆盖解析后的 plugin/knowledge/workflow 列表。

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.transformer.Transformer

将配置字典转换为平台 LLM Agent DSL JSON 字符串（无状态，可多次调用）。

### transform_to_dsl(agent_info: dict, resource: Dict[str, Any]) -> str

根据 `agent_info` 与 `resource`（含 `plugin_dict`、`tool_id_map`、`workflow_dict` 等）生成 DSL 字符串。

### staticmethod collect_plugin / collect_workflow / convert_input_parameters / convert_output_parameters / build_plugin_dependencies / build_workflow_dependencies

辅助方法，用于拼装插件与工作流依赖结构（供 DSL 构造使用）。

---

## class openjiuwen.dev_tools.agent_builder.builders.llm_agent.intention_detector.IntentionDetector

判断当前用户输入是否希望对已澄清需求做优化（`need_refined`）。

**参数**：

* **llm**：大模型实例（与 [Model](../../../../openjiuwen.core/foundation/llm/llm.md) 一致）。

### staticmethod extract_intent(inputs: str) -> Dict[str, Any]

从模型输出中提取 JSON 意图结果。

### detect_refine_intent(query, agent_config_info) -> bool

返回是否需要重新澄清（内部调用 LLM）。

**异常**：

* **ApplicationError**：意图检测异常。
