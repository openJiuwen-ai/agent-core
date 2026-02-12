# openjiuwen.core.workflow

`openjiuwen.core.workflow.components.llm.llm_comp` 模块提供工作流中的大模型（LLM）组件，用于在工作流节点中调用大模型完成文本生成、结构化输出等任务。支持通过 [LLMCompConfig](llm_comp.md#class-openjiuwencoreworkflowcomponentsllmllm_compllmcompconfig) 配置模型、提示词模板、响应格式（text / markdown / json）与输出字段；支持非流式invoke与流式stream。组件通过 `openjiuwen.core.workflow` 导出，建议使用 `from openjiuwen.core.workflow import LLMComponent, LLMCompConfig` 导入。更多组件说明见 [components](../../components.README.md)。

## class openjiuwen.core.workflow.components.llm.llm_comp.LLMCompConfig

LLM 组件的配置数据类，继承自 [ComponentConfig](../components.md)。

* **model_id**（str | None）：已注册模型 id，从 [Runner](../../../runner/runner.md) 资源管理器获取模型；与 `model_client_config` + `model_config` 二选一。
* **model_client_config**（ModelClientConfig | None）：模型客户端配置，与 `model_config` 配合使用，用于直接构造 [Model](../../../foundation/llm.README.md)。
* **model_config**（ModelRequestConfig | None）：模型请求配置（如温度、最大 token 等）。
* **template_content**（list[Any]）：提示词模板内容，为消息列表（含 `role`、`content`），须至少包含一条 `user` 消息，`system` 消息须在 `user` 之前。
* **system_prompt_template**（SystemMessage | None）：可选系统提示词模板。
* **user_prompt_template**（UserMessage | None）：可选用户提示词模板。
* **response_format**（dict[str, Any]）：响应格式，`type` 为 `"text"`、`"markdown"` 或 `"json"`；json 时可配 `jsonInstruction` 等。
* **output_config**（dict[str, Any]）：输出字段定义，与 `response_format` 配合；text/markdown 时仅允许一个字段，json 时为多字段及类型描述。
* **enable_history**（bool）：是否将上下文中对话历史拼入模型输入，默认 `False`。

---

## class openjiuwen.core.workflow.components.llm.llm_comp.LLMComponent

```python
class openjiuwen.core.workflow.components.llm.llm_comp.LLMComponent(component_config: Optional[LLMCompConfig] = None)
```

大模型组件，实现 [ComponentComposable](../components.md)。根据 [LLMCompConfig](llm_comp.md#class-openjiuwencoreworkflowcomponentsllmllm_compllmcompconfig) 构造LLMExecutable，在 [to_executable](llm_comp.md#to_executable---llmexecutable) 时校验模板、响应格式与输出配置，并创建可执行实例。

**参数**：

- **component_config**（LLMCompConfig | None）：LLM 组件配置；为 `None` 时由可执行对象侧校验。

### to_executable() -> LLMExecutable

返回LLMExecutable实例。首次调用时会校验 `template_content`、`response_format`、`output_config`。

**返回**：

- **LLMExecutable**：本组件的可执行实现。

### property executable() -> LLMExecutable

懒加载的可执行实例，等价于首次调用 [to_executable](llm_comp.md#to_executable---llmexecutable) 的结果。

**返回**：

- **LLMExecutable**：本组件的可执行实现。
