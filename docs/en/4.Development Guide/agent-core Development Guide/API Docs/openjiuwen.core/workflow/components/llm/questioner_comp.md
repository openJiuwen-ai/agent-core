# openjiuwen.core.workflow

The `openjiuwen.core.workflow.components.llm.questioner_comp` module provides questioner components in workflows, used to extract parameters from user input or conversation history through large language models based on configured field information, and ask users through human-computer interaction when required fields are missing, until all are collected or maximum question count is reached. Components are exported through `openjiuwen.core.workflow`, it is recommended to use `from openjiuwen.core.workflow import QuestionerComponent, QuestionerConfig, FieldInfo` for import. For more component information, see [components](../../components.README.md).

## class openjiuwen.core.workflow.components.llm.questioner_comp.FieldInfo

```python
class openjiuwen.core.workflow.components.llm.questioner_comp.FieldInfo()
```

Description of a single field to be extracted/questioned, used for `field_names` in [QuestionerConfig](./questioner_comp.md#class-openjiuwencoreworkflowcomponentsllmquestioner_compquestionerconfig).

* **field_name** (str): Field name, corresponds to key in LLM output and state.
* **description** (str): Field description, used to construct prompt.
* **type** (Literal["string", "integer", "number", "boolean"]): Field type, default `"string"`, used for validation and conversion of extraction results.
* **cn_field_name** (str): Chinese display name, used for question prompts, default `""`.
* **required** (bool): Whether required, default `False`; required fields that are not extracted will generate questions.
* **default_value** (Any): Default value, default `""`; when not extracted and default value is configured, write to output.

---

## class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerConfig

```python
class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerConfig()
```

Questioner component configuration data class.

* **model_id** (str | None): Registered model id, mutually exclusive with `model_client_config` + `model_config`.
* **model_client_config** (ModelClientConfig | None): Model client configuration.
* **model_config** (ModelRequestConfig | None): Model request configuration.
* **response_type** (str): Response type, currently supports `ResponseType.ReplyDirectly` (`"reply_directly"`), default `"reply_directly"`.
* **question_content** (str): Fixed question content template (optional), if configured takes priority, supports `{{key}}` placeholders; mutually exclusive or combined use with `field_names` extraction.
* **extract_fields_from_response** (bool): Whether to extract fields in `field_names` from user/model response, default `True`.
* **field_names** (list[FieldInfo]): List of fields to be extracted, see [FieldInfo](./questioner_comp.md#class-openjiuwencoreworkflowcomponentsllmquestioner_compfieldinfo); in extraction mode, at least one must be configured and `field_name` must be non-empty.
* **max_response** (int): Maximum question rounds, must be greater than 0; after exceeding, if there are still required fields not filled, will throw error.
* **with_chat_history** (bool): Whether to use conversation history from context for extraction, default `False`.
* **chat_history_max_rounds** (int): Maximum number of conversation history rounds to use for extraction, default 5.
* **extra_prompt_for_fields_extraction** (str): Additional constraint description for extraction, default `""`.
* **example_content** (str): Example content, used for prompts, default `""`.

---

## class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerComponent

```python
class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerComponent(questioner_comp_config: QuestionerConfig = None)
```

Questioner component, implements [ComponentComposable](../components.md). Constructs QuestionerExecutable according to [QuestionerConfig](./questioner_comp.md#class-openjiuwencoreworkflowcomponentsllmquestioner_compquestionerconfig); executable object loads/restores questioner state from session during `invoke`, decides whether to ask directly, extract from history or end and output based on state and configuration, calls [session.interact](../../../session/session.md) for human-computer interaction when necessary.

**Parameters**:

- **questioner_comp_config** (QuestionerConfig | None): Questioner configuration; when `None`, validation is done by executable object side (will require required items like `field_names`, `max_response`, etc.).

### to_executable() -> Executable

Returns QuestionerExecutable instance, internally validates `response_type`, `extract_fields_from_response` and `field_names`, `max_response`.

**Returns**:

- **Executable**: That is `QuestionerExecutable(self._questioner_config).state(QuestionerState())`.
