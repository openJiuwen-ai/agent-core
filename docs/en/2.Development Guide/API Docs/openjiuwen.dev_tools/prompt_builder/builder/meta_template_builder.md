# openjiuwen.dev_tools.prompt_builder.builder.meta_template_builder

## class openjiuwen.dev_tools.prompt_builder.builder.meta_template_builder.MetaTemplateBuilder

`MetaTemplateBuilder` is a "prompt generation/rewrite" optimizer.

It converts input "task description/instruction draft + tool list" into more standardized, more executable prompt text through a set of built-in meta-templates (and optional custom meta-templates).

## Applicable Scenarios

- You only have a natural language "task description/requirement", want to quickly generate a structured prompt.
- You hope to switch between different template strategies like "general/plan".
- You hope to inject tool list, so generated prompt can correctly guide model to use tools.

## Initialization

```python
MetaTemplateBuilder(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

**Parameters**:

- **model_config** (`ModelRequestConfig`): Model request configuration.
  - Recommended to explicitly set temperature/top_p/max_tokens and other parameters to control generation style and length.
  - This is model configuration for "optimization task", not necessarily the same as your business Agent's configuration.
- **model_client_config** (`ModelClientConfig`): Model client configuration.
  - Used to specify provider/base_url/api_key and other connection information.
  - Recommended to inject in controlled environments, avoid leaking sensitive fields in documents/logs.

## register_meta_template

```python
register_meta_template(name: str, meta_template: str | PromptTemplate)
```

Register custom meta-template.

When you use `build(..., template_type="other", custom_template_name=...)`, template will be retrieved from here.

**Parameters**:

- **name** (str): Template name (without prefix).
  - Internally automatically adds fixed prefix `META_TEMPLATE_`.
  - Recommended to use stable and readable naming (e.g., `my_team_general_v1`).
- **meta_template** (str | PromptTemplate): Template content.
  - When passing `str`, will automatically wrap as `PromptTemplate(content=...)`.
  - When passing `PromptTemplate`, will deepcopy a copy to avoid external references being accidentally modified.
  - Only supports `str` or `PromptTemplate`; other types will throw exception.

**Exceptions**:

- When `meta_template` type is invalid, throws `build_error(StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR, ...)`.

**Example**:

```python
>>> # 1. Custom meta template prompt
>>> template = "this is a string meta template"
>>>
>>> # 2. Create model configuration
>>> Modelclientconfig = ModelClientConfig(
...     api_key=API_KEY,
...     api_base=API_BASE,
...     client_id="demo",
...     verify_ssl=False,
...     client_provider="xxxx"
... )
>>> Modelrequestconfig = ModelRequestConfig(model=MODEL_NAME)
>>>
>>> # 3. Create MetaTemplateBuilder object
>>> meta_template_builder = MetaTemplateBuilder(Modelrequestconfig, Modelclientconfig)
>>> 
>>> # 4. Register meta template prompt
>>> meta_template_builder.register_meta_template("custom_template", template)
```

## build

```python
async build(prompt: str | PromptTemplate, tools: Optional[List[ToolInfo]] = None, template_type: Literal["general", "plan", "other"] = "general", custom_template_name: Optional[str] = None) -> Optional[str]
```

Generates/optimizes prompt text (non-streaming).

**Parameters**:

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate))): Original prompt/task description.
  - Allows passing `PromptTemplate`, will first convert to pure string content then process.
  - Cannot be empty or whitespace string, otherwise will throw exception.
- **tools** (List[ToolInfo] | None): Available tool list.
  - After passing, will be written to `tools` field of meta-template, helping model generate prompt that "can correctly call tools".
  - Requires each element must be `ToolInfo`, otherwise will throw exception.
- **template_type**: Template strategy.
  - `"general"`: General generation strategy (default).
  - `"plan"`: Planning/step-by-step generation strategy.
  - `"other"`: Use custom template (must also provide `custom_template_name`).
- **custom_template_name**: Custom template name.
  - Only used when `template_type="other"`.
  - Must first register template with same name through `register_meta_template`, otherwise will throw exception.

**Returns**:

- **Optional[str]**: Generated prompt text.
  - When underlying model returns empty, may return `None`.

**Exceptions**:

- When `meta_template` type is invalid, throws `build_error(StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR, ...)`.

**Example**:

```python
>>> # 1. Create model configuration
>>> Modelclientconfig = ModelClientConfig(
...     api_key=API_KEY,
...     api_base=API_BASE,
...     client_id="demo",
...     verify_ssl=False,
...     client_provider="xxxx"
... )
>>> Modelrequestconfig = ModelRequestConfig(model=MODEL_NAME)
>>>
>>> # 2. Create MetaTemplateBuilder object
>>> builder = MetaTemplateBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. Execute prompt generation
>>> async def main():
>>>     response = await builder.build(prompt=pront, language="en-US", template_type="general")
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

Based on user-provided original prompt and selected meta template, streamingly generates prompt with richer content, more complete structure, and more rigorous logic.

**Parameters**:

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate))): Original prompt/task description.
  - Allows passing `PromptTemplate`, will first convert to pure string content then process.
  - Cannot be empty or whitespace string, otherwise will throw exception.
- **tools** (List[ToolInfo]] | None): Available tool list.
  - After passing, will be written to `tools` field of meta-template, helping model generate prompt that "can correctly call tools".
  - Requires each element must be `ToolInfo`, otherwise will throw exception.
- **template_type**: Template strategy.
  - `"general"`: General generation strategy (default).
  - `"plan"`: Planning/step-by-step generation strategy.
  - `"other"`: Use custom template (must also provide `custom_template_name`).
- **custom_template_name**: Custom template name.
  - Only used when `template_type="other"`.
  - Must first register template with same name through `register_meta_template`, otherwise will throw exception.

**Returns**:

* **AsyncGenerator**, streaming generation of prompt content, iterator.

**Exceptions**:

- When `meta_template` type is invalid, throws `build_error(StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR, ...)`.

**Example**:

```python
>>> # 1. Create model configuration
>>> Modelclientconfig = ModelClientConfig(
...     api_key=API_KEY,
...     api_base=API_BASE,
...     client_id="demo",
...     verify_ssl=False,
...     client_provider="xxxx"
... )
>>> Modelrequestconfig = ModelRequestConfig(model=MODEL_NAME)
>>>
>>> # 2. Create MetaTemplateBuilder object
>>> builder = MetaTemplateBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. Execute prompt generation
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
