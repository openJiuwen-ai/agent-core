# openjiuwen.agent_evolving.sharing.keyword_extractor

`openjiuwen.agent_evolving.sharing.keyword_extractor` is the **keyword extractor** for the experience sharing module, responsible for:

- Upload path: parsing keywords/summary directly from the optimizer's `EvolutionPatch` output, with no extra LLM call;
- Download path: running a focused LLM call against a conversation excerpt to extract retrieval keywords (10-20), returning `QueryKeywords`. On LLM failure, returns empty keywords so the calling rail never blocks.

---

## class openjiuwen.agent_evolving.sharing.keyword_extractor.KeywordExtractor

Dual-path keyword processing: upload path parses optimizer output, download path calls LLM for retrieval keywords.

```text
class KeywordExtractor(
    llm: Optional[Model] = None,
    model: Optional[str] = None,
    language: str = "cn",
    query_llm_policy: LLMInvokePolicy = QUERY_KEYWORDS_LLM_POLICY,
)
```

**Parameters**:

* **llm**(Model, optional): LLM instance for download-path keyword extraction. Default: `None`.
* **model**(str, optional): Model name. Default: `None`.
* **language**(str, optional): Prompt language, supports `"cn"` and `"en"`. Default: `"cn"`.
* **query_llm_policy**(LLMInvokePolicy, optional): LLM invocation policy (timeout, retries, etc.). Default: `QUERY_KEYWORDS_LLM_POLICY`.

### update_llm(llm, model) -> None

Updates the LLM instance and model name (late binding).

**Parameters**:

* **llm**(Model | None): New LLM instance.
* **model**(str | None): New model name.

### staticmethod parse_from_optimizer_output(raw_patch) -> Tuple[List[str], str]

Reads keywords/summary from either an `EvolutionPatch` object or a raw JSON dict. Used on the upload path with no extra LLM call.

**Parameters**:

* **raw_patch**(EvolutionPatch | dict): Optimizer output — either an in-memory `EvolutionPatch` or the raw JSON dict from the LLM.

**Returns**:

**Tuple[List[str], str]**, `(keywords, summary)` tuple. Keywords is a list of non-empty strings; summary is a stripped string.

**Example**:

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

Extracts retrieval keywords from a conversation excerpt. Used on the download path; calls LLM to generate 10-20 keywords and a query intent. On LLM failure, returns empty keywords so the calling rail never blocks.

**Parameters**:

* **feedback_excerpt**(str): Conversation excerpt (user queries, tool execution results, etc.).
* **skill_hint**(str, optional): Current skill hint. Default: `None`.

**Returns**:

**QueryKeywords**, containing keywords, intent, and raw_excerpt. Returns empty keywords when excerpt is empty or LLM is unbound.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.sharing import KeywordExtractor, QueryKeywords
>>> from openjiuwen.core.foundation.llm import Model, ModelRequestConfig, ModelClientConfig
>>>
>>> async def demo():
>>>     extractor = KeywordExtractor(language="en")
>>>     result = await extractor.extract_query_keywords(
>>>         feedback_excerpt="User encountered permission denied error when running bash command",
>>>         skill_hint="bash_tool",
>>>     )
>>>     print(result.keywords, result.intent)
>>>
>>> asyncio.run(demo())
['bash', 'permission denied', 'shell'] bash permission error troubleshooting
```

---

## class openjiuwen.agent_evolving.sharing.keyword_extractor.QUERY_KEYWORDS_LLM_POLICY

Default `LLMInvokePolicy` instance for download-path keyword extraction, controlling LLM call timeout, retries, and backoff behavior for `KeywordExtractor.extract_query_keywords`.

* **attempt_timeout_secs**(float): Per-attempt LLM call timeout in seconds. Default: `1500`.
* **total_budget_secs**(float): Total time budget in seconds; all retries are terminated when exceeded. Default: `4000`.
* **max_attempts**(int, optional): Maximum number of call attempts. Default: `5`.
* **backoff_base_secs**(float, optional): Exponential backoff base seconds; each retry waits `backoff_base_secs × 2^(attempt-1)`. Default: `1.0`.
* **retry_empty_response**(bool, optional): Whether to retry when LLM returns an empty response. Default: `True`.