# openjiuwen.core.workflow

`openjiuwen.core.workflow.components.llm.questioner_comp` 模块提供工作流中的提问器组件，用于根据配置的字段信息通过大模型从用户输入或对话历史中抽取参数，并在缺失必填项时通过人机交互向用户追问，直至收齐或达到最大追问次数。组件通过 `openjiuwen.core.workflow` 导出，建议使用 `from openjiuwen.core.workflow import QuestionerComponent, QuestionerConfig, FieldInfo` 导入。更多组件说明见 [components](../../components.README.md)。

## class openjiuwen.core.workflow.components.llm.questioner_comp.FieldInfo

```python
class openjiuwen.core.workflow.components.llm.questioner_comp.FieldInfo()
```

单个待抽取/追问字段的描述，用于 [QuestionerConfig](questioner_comp.md#class-openjiuwencoreworkflowcomponentsllmquestioner_compquestionerconfig) 的 `field_names`。

* **field_name**（str）：字段名，与 LLM 输出及状态中的 key 对应。
* **description**（str）：字段说明，用于构造提示词。
* **type**（Literal["string", "integer", "number", "boolean"]）：字段类型，默认 `"string"`，用于抽取结果的校验与转换。
* **cn_field_name**（str）：中文展示名，用于追问提示，默认 `""`。
* **required**（bool）：是否必填，默认 `False`；必填且未抽取到时会产生追问。
* **default_value**（Any）：默认值，默认 `""`；当未抽取到且配置了默认值时写入输出。

---

## class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerConfig

```python
class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerConfig()
```

提问器组件的配置数据类。

* **model_id**（str | None）：已注册模型 id，与 `model_client_config` + `model_config` 二选一。
* **model_client_config**（ModelClientConfig | None）：模型客户端配置。
* **model_config**（ModelRequestConfig | None）：模型请求配置。
* **response_type**（str）：响应类型，当前支持 `ResponseType.ReplyDirectly`（`"reply_directly"`），默认 `"reply_directly"`。
* **question_content**（str）：固定提问内容模板（可选），若配置则优先使用，支持 `{{key}}` 占位符；与 `field_names` 抽取二选一或组合使用。
* **extract_fields_from_response**（bool）：是否从用户/模型回复中抽取 `field_names` 中的字段，默认 `True`。
* **field_names**（list[FieldInfo]）：待抽取字段列表，见 [FieldInfo](questioner_comp.md#class-openjiuwencoreworkflowcomponentsllmquestioner_compfieldinfo)；抽取模式下至少配置一个且 `field_name` 非空。
* **max_response**（int）：最大追问轮数，须大于 0；超过后若仍有必填未填会抛错。
* **with_chat_history**（bool）：是否使用上下文中对话历史参与抽取，默认 `False`。
* **chat_history_max_rounds**（int）：参与抽取的对话历史最大轮数，默认 5。
* **extra_prompt_for_fields_extraction**（str）：抽取时的额外约束说明，默认 `""`。
* **example_content**（str）：示例内容，用于提示词，默认 `""`。

---

## class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerComponent

```python
class openjiuwen.core.workflow.components.llm.questioner_comp.QuestionerComponent(questioner_comp_config: QuestionerConfig = None)
```

提问器组件，实现 [ComponentComposable](../components.md)。根据 [QuestionerConfig](questioner_comp.md#class-openjiuwencoreworkflowcomponentsllmquestioner_compquestionerconfig) 构造 QuestionerExecutable；可执行对象在 `invoke` 时加载/恢复会话中的提问器状态，按状态与配置决定是直接提问、从历史抽取还是结束并输出，必要时调用 [session.interact](../../../session/session.md) 进行人机交互。

**参数**：

- **questioner_comp_config**（QuestionerConfig | None）：提问器配置；为 `None` 时由可执行对象侧校验（会要求必填项如 `field_names`、`max_response` 等）。

### to_executable() -> Executable

返回 QuestionerExecutable 实例，内部会校验 `response_type`、`extract_fields_from_response` 与 `field_names`、`max_response`。

**返回**：

- **Executable**：即 `QuestionerExecutable(self._questioner_config).state(QuestionerState())`。

