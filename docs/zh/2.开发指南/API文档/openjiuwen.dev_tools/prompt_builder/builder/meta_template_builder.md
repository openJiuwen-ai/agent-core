# openjiuwen.dev_tools.prompt_builder.builder.meta_template_builder

## class openjiuwen.dev_tools.prompt_builder.builder.meta_template_builder.MetaTemplateBuilder

`MetaTemplateBuilder` 为“提示词生成/重写”优化器。

它通过一组内置的 meta-template（以及可选的自定义 meta-template），把输入的“任务说明/指令草稿 + 工具列表”转换为更规范、更可执行的提示词文本。

## 适用场景

- 你只有一段自然语言的“任务描述/需求”，想快速生成一版结构化提示词。
- 你希望在“通用/规划（plan）”等不同模板策略之间切换。
- 你希望注入工具列表，让生成的提示词能正确引导模型使用工具。

## 初始化

```python
MetaTemplateBuilder(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

**参数**：

- **model_config** (`ModelRequestConfig`)：模型请求配置。
  - 建议明确 temperature/top_p/max_tokens 等参数，以控制生成风格与长度。
  - 这是“优化任务”的模型配置，不一定等同于你业务 Agent 的配置。
- **model_client_config** (`ModelClientConfig`)：模型客户端配置。
  - 用于指定 provider/base_url/api_key 等连接信息。
  - 建议在受控环境中注入，避免在文档/日志中泄露敏感字段。

## register_meta_template

```python
register_meta_template(name: str, meta_template: str | PromptTemplate)
```

注册自定义 meta-template。

当你在 `build(..., template_type="other", custom_template_name=...)` 时，会从这里取模板。

**参数**：

- **name** (str)：模板名（不含前缀）。
  - 内部会自动加上固定前缀 `META_TEMPLATE_`。
  - 建议用稳定且可读的命名（例如 `my_team_general_v1`）。
- **meta_template** (str | PromptTemplate)：模板内容。
  - 传 `str` 时会自动包装为 `PromptTemplate(content=...)`。
  - 传 `PromptTemplate` 时会 deepcopy 一份，避免外部引用被意外修改。
  - 只支持 `str` 或 `PromptTemplate`；其他类型会抛出异常。

**异常**：

- 当 `meta_template` 类型不合法时会抛出 `build_error(StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR, ...)`。

**样例**：

```python
>>> # 1. 自定义元模板提示词
>>> template = "this is a string meta template"
>>>
>>> # 2. 创建模型配置
>>> Modelclientconfig = ModelClientConfig(
...     api_key=API_KEY,
...     api_base=API_BASE,
...     client_id="demo",
...     verify_ssl=False,
...     client_provider="xxxx"
... )
>>> Modelrequestconfig = ModelRequestConfig(model=MODEL_NAME)
>>>
>>> # 3. 创建MetaTemplateBuilder对象
>>> meta_template_builder = MetaTemplateBuilder(Modelrequestconfig, Modelclientconfig)
>>> 
>>> # 4. 注册元模板提示词
>>> meta_template_builder.register_meta_template("custom_template", template)
```

## build

```python
async build(prompt: str | PromptTemplate, tools: Optional[List[ToolInfo]] = None, template_type: Literal["general", "plan", "other"] = "general", custom_template_name: Optional[str] = None) -> Optional[str]
```

生成/优化提示词文本（非流式）。

**参数**：

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate)))：原始提示词/任务说明。
  - 允许传 `PromptTemplate`，会先转成纯字符串内容再处理。
  - 不能为空且不能是空白字符串，否则会抛出异常。
- **tools** (List[ToolInfo] | None)：可用工具列表。
  - 传入后会被写入 meta-template 的 `tools` 字段，帮助模型生成“可正确调用工具”的提示词。
  - 要求每个元素必须是 `ToolInfo`，否则会抛出异常。
- **template_type**：模板策略。
  - `"general"`：通用生成策略（默认）。
  - `"plan"`：偏规划/分步的生成策略。
  - `"other"`：使用自定义模板（必须同时提供 `custom_template_name`）。
- **custom_template_name**：自定义模板名。
  - 仅当 `template_type="other"` 时使用。
  - 必须先通过 `register_meta_template` 注册同名模板，否则会抛出异常。

**返回**：

- **Optional[str]**：生成后的提示词文本。
  - 当底层模型返回为空时，可能返回 `None`。

**异常**：

- 当 `meta_template` 类型不合法时会抛出 `build_error(StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR, ...)`。

**样例**：

```python
>>> # 1. 创建模型配置
>>> Modelclientconfig = ModelClientConfig(
...     api_key=API_KEY,
...     api_base=API_BASE,
...     client_id="demo",
...     verify_ssl=False,
...     client_provider="xxxx"
... )
>>> Modelrequestconfig = ModelRequestConfig(model=MODEL_NAME)
>>>
>>> # 2. 创建MetaTemplateBuilder对象
>>> builder = MetaTemplateBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. 执行提示词生成
>>> async def main():
>>>     response = await builder.build(prompt="你是一个旅行助手", template_type="general")
>>>     print(response)
>>> asyncio.run(main())
## 人设
旅行助手
定义你将扮演的角色或身份：一位经验丰富的旅行规划师，熟悉全球各地的旅游景点和文化特色。
列举角色的专业技能或特长：擅长制定个性化的旅行计划，提供交通、住宿、餐饮和活动建议，能够根据用户的预算和兴趣定制行程。
（省略更多内容）
```

## stream_build

```python
async stream_build(prompt: str | PromptTemplate, tools: Optional[List[ToolInfo]] = None, template_type: Literal["general", "plan", "other"] = "general", custom_template_name: Optional[str] = None) -> AsyncGenerator
```

基于用户提供的原始提示词及所选定的元模板，流式生成内容更加丰富、结构更加完整、逻辑更加严谨的提示词。

**参数**：

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate)))：原始提示词/任务说明。
  - 允许传 `PromptTemplate`，会先转成纯字符串内容再处理。
  - 不能为空且不能是空白字符串，否则会抛出异常。
- **tools** (List[ToolInfo]] | None)：可用工具列表。
  - 传入后会被写入 meta-template 的 `tools` 字段，帮助模型生成“可正确调用工具”的提示词。
  - 要求每个元素必须是 `ToolInfo`，否则会抛出异常。
- **template_type**：模板策略。
  - `"general"`：通用生成策略（默认）。
  - `"plan"`：偏规划/分步的生成策略。
  - `"other"`：使用自定义模板（必须同时提供 `custom_template_name`）。
- **custom_template_name**：自定义模板名。
  - 仅当 `template_type="other"` 时使用。
  - 必须先通过 `register_meta_template` 注册同名模板，否则会抛出异常。

**返回**：

* **AsyncGenerator**，流式生成提示词内容，迭代器。

**异常**：

- 当 `meta_template` 类型不合法时会抛出 `build_error(StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR, ...)`。

**样例**：

```python
>>> # 1. 创建模型配置
>>> Modelclientconfig = ModelClientConfig(
...     api_key=API_KEY,
...     api_base=API_BASE,
...     client_id="demo",
...     verify_ssl=False,
...     client_provider="xxxx"
... )
>>> Modelrequestconfig = ModelRequestConfig(model=MODEL_NAME)
>>>
>>> # 2. 创建MetaTemplateBuilder对象
>>> builder = MetaTemplateBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. 执行提示词生成
>>> async def main():
>>>     async for chunk in builder.stream_build(prompt="you are a calendar assistant", template_type="plan", language="en-US"):
...         print(chunk)
>>> asyncio.run(main())
##
人
设
定义
你
将
扮演
的角色
或
身份
（省略更多内容）
```

