# openjiuwen.dev_tools.prompt_builder.builder.bad_case_prompt_builder

## class openjiuwen.dev_tools.prompt_builder.builder.bad_case_prompt_builder.BadCasePromptBuilder

`BadCasePromptBuilder` 为“错误案例（Bad Case）驱动的提示词优化器”。

它会读取一组评测失败用例（`EvaluatedCase`），先总结“失败原因与改进建议”，再据此对原始提示词进行定向修复。

这类优化特别适合：

- 边缘场景频繁失败（例如格式要求严格、工具调用易错、强约束任务）
- 你已经有评测集与 `EvaluatedCase`（包含 answer/score/reason）
- 希望把“坏例反思”系统化沉淀到提示词里

## 初始化

```python
BadCasePromptBuilder(model_config: ModelRequestConfig, model_client_config: ModelClientConfig)
```

参数语义与 `MetaTemplateBuilder` 一致。

## build

```python
async build(prompt: str | PromptTemplate, cases: List[EvaluatedCase]) -> Optional[str]
```

基于坏例对提示词进行定向优化（非流式）。

**参数**：

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate))：原始提示词。
  - 不能为空且不能全空白。
  - 若是 `PromptTemplate`，会先抽取成纯字符串内容参与优化。
- **cases** (List[[EvaluatedCase](../../tune/base.md#class-openjiuwendev_toolstunebaseevaluatedcase)])：评测结果用例列表。
  - 必须非空；否则会抛出异常。
  - 框架默认限制 cases 数量上限（源码默认 10），避免一次性塞入过多 bad cases 导致上下文过长。
  - `EvaluatedCase` 内应包含：用例 inputs/label、模型 answer、评分 score（0~1）、失败原因 reason。
    - 一般推荐只传“失败样本”（例如 score=0 的样本），让优化更聚焦。

**返回**：

- **Optional[str]**：优化后的提示词文本。

**异常（常见）**：

- 当 `prompt` 为空/全空白时抛出 `build_error(StatusCode.TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR, ...)`。
- 当 `cases` 为空或超过上限时抛出同类错误。
  - 这类校验用于避免“无训练信号”或“上下文爆炸”。

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
>>> # 2. 创建BadCasePromptBuilder对象
>>> badcase_builder = BadCasePromptBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3. 构造错误案例
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
>>> # 4. 执行提示词错误案例优化
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

基于用户提供的原始提示词及若干典型错误示例，通过系统化分析与重构，对提示词内容进行精准优化，流式生成优化后的提示词。

**参数**：

- **prompt** (str | [PromptTemplate](../../../openjiuwen.core/foundation/prompt/template.md#class-openjiuwencorefoundationprompttemplateprompttemplate)：原始提示词。
  - 不能为空且不能全空白。
  - 若是 `PromptTemplate`，会先抽取成纯字符串内容参与优化。
- **cases** (List[[EvaluatedCase](../../tune/base.md#class-openjiuwendev_toolstunebaseevaluatedcase)])：评测结果用例列表。
  - 必须非空；否则会抛出异常。
  - 框架默认限制 cases 数量上限（源码默认 10），避免一次性塞入过多 bad cases 导致上下文过长。
  - `EvaluatedCase` 内应包含：用例 inputs/label、模型 answer、评分 score（0~1）、失败原因 reason。
    - 一般推荐只传“失败样本”（例如 score=0 的样本），让优化更聚焦。
    - 
**返回**：

* **AsyncGenerator**，返回优化后提示词的流式输出迭代器。

**异常（常见）**：

- 当 `prompt` 为空/全空白时抛出 `build_error(StatusCode.TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR, ...)`。
- 当 `cases` 为空或超过上限时抛出同类错误。
  - 这类校验用于避免“无训练信号”或“上下文爆炸”。

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
>>> # 2. 创建BadCasePromptBuilder对象
>>> badcase_builder = BadCasePromptBuilder(Modelrequestconfig, Modelclientconfig)
>>>
>>> # 3.构造错误案例
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
>>> # 4. 执行提示词错误案例优化
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

