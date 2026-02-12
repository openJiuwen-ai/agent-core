# openjiuwen.core.workflow

The `openjiuwen.core.workflow.components.llm.llm_comp` module provides large language model (LLM) components in workflows, used to call large language models in workflow nodes to complete tasks like text generation and structured output. Supports configuring model, prompt template, response format (text / markdown / json) and output fields through [LLMCompConfig](./llm_comp.md#class-openjiuwencoreworkflowcomponentsllmllm_compllmcompconfig); supports non-streaming invoke and streaming stream. Components are exported through `openjiuwen.core.workflow`, it is recommended to use `from openjiuwen.core.workflow import LLMComponent, LLMCompConfig` for import. For more component information, see [components](../../components.README.md).

## class openjiuwen.core.workflow.components.llm.llm_comp.LLMCompConfig

LLM component configuration data class, inherits from [ComponentConfig](../components.md).

* **model_id** (str | None): Registered model id, get model from [Runner](../../../runner/runner.md) resource manager; mutually exclusive with `model_client_config` + `model_config`.
* **model_client_config** (ModelClientConfig | None): Model client configuration, used together with `model_config` to directly construct [Model](../../../foundation/llm.README.md).
* **model_config** (ModelRequestConfig | None): Model request configuration (e.g., temperature, max tokens, etc.).
* **template_content** (list[Any]): Prompt template content, is a message list (containing `role`, `content`), must contain at least one `user` message, `system` message must be before `user`.
* **system_prompt_template** (SystemMessage | None): Optional system prompt template.
* **user_prompt_template** (UserMessage | None): Optional user prompt template.
* **response_format** (dict[str, Any]): Response format, `type` is `"text"`, `"markdown"` or `"json"`; when json, can configure `jsonInstruction`, etc.
* **output_config** (dict[str, Any]): Output field definitions, used together with `response_format`; text/markdown allows only one field, json is multi-field and type description.
* **enable_history** (bool): Whether to include conversation history from context into model input, default `False`.

---

## class openjiuwen.core.workflow.components.llm.llm_comp.LLMComponent

```python
class openjiuwen.core.workflow.components.llm.llm_comp.LLMComponent(component_config: Optional[LLMCompConfig] = None)
```

Large language model component, implements [ComponentComposable](../components.md). Constructs LLMExecutable according to [LLMCompConfig](./llm_comp.md#class-openjiuwencoreworkflowcomponentsllmllm_compllmcompconfig), validates template, response format and output configuration when [to_executable](./llm_comp.md#to_executable---llmexecutable), and creates executable instance.

**Parameters**:

- **component_config** (LLMCompConfig | None): LLM component configuration; when `None`, validation is done by executable object side.

### to_executable() -> LLMExecutable

Returns LLMExecutable instance. First call will validate `template_content`, `response_format`, `output_config`.

**Returns**:

- **LLMExecutable**: Executable implementation of this component.

### property executable() -> LLMExecutable

Lazy-loaded executable instance, equivalent to result of first call to [to_executable](./llm_comp.md#to_executable---llmexecutable).

**Returns**:

- **LLMExecutable**: Executable implementation of this component.
