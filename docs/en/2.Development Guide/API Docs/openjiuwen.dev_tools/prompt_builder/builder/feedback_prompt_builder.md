# openjiuwen.dev_tools.prompt_builder.builder.feedback_prompt_builder

## class openjiuwen.dev_tools.prompt_builder.builder.feedback_prompt_builder.FeedbackPromptBuilder

`FeedbackPromptBuilder` is a "feedback-based prompt optimizer".

It is suitable when you already have a version of prompt, but have received clear improvement suggestions from users/reviewers/online feedback, and hope to convert these feedbacks into "better prompt text".

## Initialization

```python
FeedbackPromptBuilder(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

Parameter semantics are consistent with `MetaTemplateBuilder`: the former controls generation style of optimization task, the latter specifies model connection configuration.

## build

```python
async build(prompt: str | PromptTemplate, feedback: str, mode: Literal["general", "insert", "select"] = "general", start_pos: Optional[int] = None, end_pos: Optional[int] = None) -> Optional[str]
```

Generates "optimized prompt text" based on feedback (non-streaming).

**Parameters**:

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate)): Original prompt.
  - Cannot be empty or all whitespace.
  - If it is `PromptTemplate`, will first extract to pure string content for optimization.
- **feedback** (str): Feedback information.
  - Cannot be empty or all whitespace.
  - Recommended to write in "problem + improvement suggestion" form (e.g., where is vague, where is missing, what constraints to add).
- **mode**: Optimization mode.
  - `"general"`: Global rewrite of entire prompt (default).
  - `"insert"`: Insert optimized content at specified position (specify insertion point through `start_pos`).
  - `"select"`: Local replacement optimization for specified range (`start_pos:end_pos`).
- **start_pos** (int, optional): Start position.
  - Must be provided in `insert` mode, and must satisfy `0 <= start_pos <= len(prompt)`.
  - Must be provided in `select` mode, and must satisfy `0 <= start_pos < end_pos <= len(prompt)`.
- **end_pos** (int, optional): End position.
  - Only required in `select` mode.
  - Must be greater than start_pos, and not exceed prompt length.

**Returns**:

- **Optional[str]**: Optimized prompt.

**Exceptions (common)**:

- When `prompt/feedback` is None or empty string, throws `build_error(StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR, ...)`.
- When `mode` is `insert/select` but position parameters are invalid, throws same type of error.
  - These validations are used to avoid "position out of bounds/illegal range" causing rewrite results to be uncontrollable.

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
>>> # 2. Create FeedbackPromptBuilder object
>>> feedback_builder = FeedbackPromptBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. Execute prompt feedback optimization
>>> async def main():
>>>     response = await feedback_builder.build(prompt="你是一个旅行助手", feedback="丰富一下")
>>>     print(response)
>>> asyncio.run(main())
你是一个专业的旅行助手，能够提供旅行规划、景点推荐和当地文化介绍
```

## stream_build

```python
async stream_build(prompt: str | PromptTemplate, feedback: str, mode: Literal["general", "insert", "select"] = "general", start_pos: Optional[int] = None, end_pos: Optional[int] = None) -> AsyncGenerator
```

Based on user-provided original prompt and corresponding feedback information, through intelligent analysis and reconstruction, streamingly generates optimized prompt, thereby improving prompt accuracy.

**Parameters**:

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate)): Original prompt.
  - Cannot be empty or all whitespace.
  - If it is `PromptTemplate`, will first extract to pure string content for optimization.
- **feedback** (str): Feedback information.
  - Cannot be empty or all whitespace.
  - Recommended to write in "problem + improvement suggestion" form (e.g., where is vague, where is missing, what constraints to add).
- **mode**: Optimization mode.
  - `"general"`: Global rewrite of entire prompt (default).
  - `"insert"`: Insert optimized content at specified position (specify insertion point through `start_pos`).
  - `"select"`: Local replacement optimization for specified range (`start_pos:end_pos`).
- **start_pos** (int, optional): Start position.
  - Must be provided in `insert` mode, and must satisfy `0 <= start_pos <= len(prompt)`.
  - Must be provided in `select` mode, and must satisfy `0 <= start_pos < end_pos <= len(prompt)`.
- **end_pos** (int, optional): End position.
  - Only required in `select` mode.
  - Must be greater than start_pos, and not exceed prompt length.

**Returns**:

* **AsyncGenerator**, streaming iterator for optimized prompt.

**Exceptions**:

**Exceptions (common)**:

- When `prompt/feedback` is None or empty string, throws `build_error(StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR, ...)`.
- When `mode` is `insert/select` but position parameters are invalid, throws same type of error.
  - These validations are used to avoid "position out of bounds/illegal range" causing rewrite results to be uncontrollable.

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
>>> # 2. Create FeedbackPromptBuilder object
>>> feedback_builder = FeedbackPromptBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. Execute prompt feedback optimization
>>> async def main():
>>>     async for chunk in feedback_builder.stream_build(prompt="你是一个旅行助手", feedback="丰富一下"):
...         print(chunk)
>>> asyncio.run(main())
你
是一个
专业的
旅行
助手
（省略更多内容）
```
