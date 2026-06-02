# openjiuwen.agent_evolving.sharing.keyword_extractor

`openjiuwen.agent_evolving.sharing.keyword_extractor` 是经验共享模块的**关键词提取器**，负责：

- 上传路径：从优化器输出的 `EvolutionPatch` 中直接解析 keywords/summary，无需额外 LLM 调用；
- 下载路径：调用 LLM 从对话摘要中抽取检索关键词（10-20 个），返回 `QueryKeywords`。LLM 失败时返回空关键词，不阻塞调用方。

---

## class openjiuwen.agent_evolving.sharing.keyword_extractor.KeywordExtractor

双路径关键词处理：上传路径解析优化器输出，下载路径调用 LLM 抽取检索关键词。

```text
class KeywordExtractor(
    llm: Optional[Model] = None,
    model: Optional[str] = None,
    language: str = "cn",
    query_llm_policy: LLMInvokePolicy = QUERY_KEYWORDS_LLM_POLICY,
)
```

**参数**：

* **llm**(Model，可选)：LLM 实例，用于下载路径的关键词抽取。默认值：`None`。
* **model**(str，可选)：模型名称。默认值：`None`。
* **language**(str，可选)：prompt 语言，支持 `"cn"` 和 `"en"`。默认值：`"cn"`。
* **query_llm_policy**(LLMInvokePolicy，可选)：LLM 调用策略（超时、重试等）。默认值：`QUERY_KEYWORDS_LLM_POLICY`。

### update_llm(llm, model) -> None

更新 LLM 实例和模型名称（延迟绑定）。

**参数**：

* **llm**(Model | None)：新的 LLM 实例。
* **model**(str | None)：新的模型名称。

### staticmethod parse_from_optimizer_output(raw_patch) -> Tuple[List[str], str]

从优化器输出中解析 keywords 和 summary。上传路径使用，无需额外 LLM 调用。

**参数**：

* **raw_patch**(EvolutionPatch | dict)：优化器输出的 `EvolutionPatch` 对象或原始 JSON dict。

**返回**：

**Tuple[List[str], str]**，`(keywords, summary)` 元组。keywords 为非空字符串列表，summary 为去除首尾空白的字符串。

**样例**：

```python
>>> from openjiuwen.agent_evolving.sharing import KeywordExtractor
>>> from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch
>>>
>>> patch = EvolutionPatch(
>>>     script="echo hello",
>>>     keywords=["bash", "shell", "error"],
>>>     summary="fix bash error",
>>> )
>>> keywords, summary = KeywordExtractor.parse_from_optimizer_output(patch)
>>> print(keywords, summary)
['bash', 'shell', 'error'] fix bash error
```

### async extract_query_keywords(feedback_excerpt, skill_hint=None) -> QueryKeywords

从对话摘要中抽取检索关键词。下载路径使用，调用 LLM 生成 10-20 个关键词及查询意图。LLM 失败时返回空关键词，不阻塞调用方。

**参数**：

* **feedback_excerpt**(str)：对话摘要片段（用户查询、工具执行结果等）。
* **skill_hint**(str，可选)：当前 Skill 提示信息。默认值：`None`。

**返回**：

**QueryKeywords**，包含 keywords、intent 和 raw_excerpt。excerpt 为空或 LLM 未绑定时返回空关键词。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.sharing import KeywordExtractor, QueryKeywords
>>> from openjiuwen.core.foundation.llm import Model, ModelRequestConfig, ModelClientConfig
>>>
>>> async def demo():
>>>     extractor = KeywordExtractor(language="cn")
>>>     result = await extractor.extract_query_keywords(
>>>         feedback_excerpt="用户执行 bash 命令时遇到 permission denied 错误",
>>>         skill_hint="bash_tool",
>>>     )
>>>     print(result.keywords, result.intent)
>>>
>>> asyncio.run(demo())
['bash', 'permission denied', 'shell'] bash权限错误排查
```

---

## class openjiuwen.agent_evolving.sharing.keyword_extractor.QUERY_KEYWORDS_LLM_POLICY

下载路径关键词抽取的默认 `LLMInvokePolicy` 实例，用于控制 `KeywordExtractor.extract_query_keywords` 的 LLM 调用超时、重试与退避行为。

* **attempt_timeout_secs**(float)：单次 LLM 调用超时秒数。默认值：`1500`。
* **total_budget_secs**(float)：总时间预算秒数，超出后终止所有重试。默认值：`4000`。
* **max_attempts**(int，可选)：最大调用尝试次数。默认值：`5`。
* **backoff_base_secs**(float，可选)：指数退避基准秒数，每次重试等待 `backoff_base_secs × 2^(attempt-1)`。默认值：`1.0`。
* **retry_empty_response**(bool，可选)：LLM 返回空内容时是否重试。默认值：`True`。