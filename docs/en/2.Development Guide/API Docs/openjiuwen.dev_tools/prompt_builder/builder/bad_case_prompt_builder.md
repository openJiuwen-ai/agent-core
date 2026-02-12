# openjiuwen.dev_tools.prompt_builder.builder.bad_case_prompt_builder

## class openjiuwen.dev_tools.prompt_builder.builder.bad_case_prompt_builder.BadCasePromptBuilder

`BadCasePromptBuilder` is a "bad case-driven prompt optimizer".

It reads a set of evaluation failed cases (`EvaluatedCase`), first summarizes "failure reasons and improvement suggestions", then performs targeted fixes on the original prompt based on this.

This type of optimization is particularly suitable for:

- Frequent failures in edge scenarios (e.g., strict format requirements, error-prone tool calls, strongly constrained tasks)
- You already have an evaluation set and `EvaluatedCase` (containing answer/score/reason)
- Hope to systematically incorporate "bad case reflection" into prompts

## Initialization

```python
BadCasePromptBuilder(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

Parameter semantics are consistent with `MetaTemplateBuilder`.

## build

```python
async build(prompt: str | PromptTemplate, cases: List[EvaluatedCase]) -> Optional[str]
```

Performs targeted optimization on prompt based on bad cases (non-streaming).

**Parameters**:

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate)): Original prompt.
  - Cannot be empty or all whitespace.
  - If it is `PromptTemplate`, will first extract to pure string content for optimization.
- **cases** (List[[EvaluatedCase](../../tune/base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]): Evaluation result case list.
  - Must be non-empty; otherwise will throw exception.
  - Framework defaults to limiting cases count upper limit (source code default 10), to avoid too many bad cases causing context to be too long.
  - `EvaluatedCase` should contain: case inputs/label, model answer, score (0~1), failure reason.
    - Generally recommended to only pass "failed samples" (e.g., samples with score=0), to make optimization more focused.

**Returns**:

- **Optional[str]**: Optimized prompt text.

**Exceptions (common)**:

- When `prompt` is empty/all whitespace, throws `build_error(StatusCode.TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR, ...)`.
- When `cases` is empty or exceeds limit, throws same type of error.
  - These validations are used to avoid "no training signal" or "context explosion".

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
>>> # 2. Create BadCasePromptBuilder object
>>> badcase_builder = BadCasePromptBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. Construct bad cases
>>> BAD_CASES = [
...     EvaluatedCase(case=Case(
...         inputs={"query": "潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
...         label={"label": "[潘之恒]"}),
...         answer={"answer": "[潘之恒, 冰华生]"}
...     ),
...     EvaluatedCase(case=Case(
...         inputs={"query": "郭造卿（1532—1593），字建初，号海岳，福建福清县化南里人（今福清市人），郭遇卿之弟，郭造卿少年的时候就很有名气，曾游学吴越"},
...         label={"label": "[郭造卿, 郭遇卿]"}),
...         answer={"answer": "[郭造卿, 郭遇卿, 吴越]"}
...     ),
... ]
>>> # 4. Execute prompt bad case optimization
>>> async def main():
>>>     response = await badcase_builder.build(prompt="你是一个人名信息提取助手", cases=BAD_CASES)
>>>     print(response)
>>> asyncio.run(main())
你是一个专业的人名信息提取助手，专注于准确提取文本中的人名，忽略其他信息，确保提取的人名完整且正确。
```

### stream_build

```python
async stream_build(prompt: str | PromptTemplate, cases: List[EvaluatedCase]) -> AsyncGenerator
```

Performs targeted optimization on prompt based on user-provided original prompt and several typical error examples, through systematic analysis and reconstruction, streamingly generates optimized prompt.

**Parameters**:

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate)): Original prompt.
  - Cannot be empty or all whitespace.
  - If it is `PromptTemplate`, will first extract to pure string content for optimization.
- **cases** (List[[EvaluatedCase](../../tune/base.md#class-openjiuwendev_toolstunebaseevaluatedcase)]): Evaluation result case list.
  - Must be non-empty; otherwise will throw exception.
  - Framework defaults to limiting cases count upper limit (source code default 10), to avoid too many bad cases causing context to be too long.
  - `EvaluatedCase` should contain: case inputs/label, model answer, score (0~1), failure reason.
    - Generally recommended to only pass "failed samples" (e.g., samples with score=0), to make optimization more focused.
    - 
**Returns**:

* **AsyncGenerator**, returns streaming output iterator for optimized prompt.

**Exceptions (common)**:

- When `prompt` is empty/all whitespace, throws `build_error(StatusCode.TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR, ...)`.
- When `cases` is empty or exceeds limit, throws same type of error.
  - These validations are used to avoid "no training signal" or "context explosion".

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
>>> # 2. Create BadCasePromptBuilder object
>>> badcase_builder = BadCasePromptBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. Construct bad cases
>>> BAD_CASES = [
...     EvaluatedCase(case=Case(
...         inputs={"query": "潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
...         label={"label": "[潘之恒]"}),
...         answer={"answer": "[潘之恒, 冰华生]"}
...     ),
...     EvaluatedCase(case=Case(
...         inputs={"query": "郭造卿（1532—1593），字建初，号海岳，福建福清县化南里人（今福清市人），郭遇卿之弟，郭造卿少年的时候就很有名气，曾游学吴越"},
...         label={"label": "[郭造卿, 郭遇卿]"}),
...         answer={"answer": "[郭造卿, 郭遇卿, 吴越]"}
...     ),
... ]
>>> # 4. Execute prompt bad case optimization
>>> async def main():
>>>     async for chunk in badcase_builder.stream_build(prompt="你是一个人名信息提取助手", cases=BAD_CASES):
...         print(chunk)
>>> asyncio.run(main())
你
是一个
专业
的人
名
信息
（省略更多内容）
```
