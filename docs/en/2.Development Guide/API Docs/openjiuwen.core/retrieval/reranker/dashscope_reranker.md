# openjiuwen.core.retrieval.reranker.dashscope_reranker

## class openjiuwen.core.retrieval.reranker.dashscope_reranker.DashscopeReranker

Subclass of [StandardReranker](./standard_reranker.md) that calls Alibaba Cloud DashScope **multimodal rerank** (`/text-rerank`) over HTTP via httpx. The request path is the class attribute `end_point` (`/services/rerank/text-rerank/text-rerank`). Unlike the `dashscope` Python SDK, this client uses async/sync HTTP and keeps the same surface as [StandardReranker](./standard_reranker.md). Product limits and model lists are described in Alibaba Cloud docs: [Text rerank API](https://www.alibabacloud.com/help/en/model-studio/text-rerank-api) (Chinese: [通用文本排序模型 API](https://help.aliyun.com/zh/model-studio/developer-reference/text-rerank-api)).

`AliyunReranker` in `openjiuwen.extensions.vendor_specific.aliyun_reranker` is deprecated; use this class instead.

```python
DashscopeReranker(config: RerankerConfig, max_retries: int = 3, retry_wait: float = 0.1, extra_headers: Optional[dict] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

Same constructor as [StandardReranker](./standard_reranker.md). `RerankerConfig` must set `api_base`, `api_key`, and the rerank model name (`model`) for your DashScope endpoint. After joining `api_base` with `end_point`, the URL should match the rerank endpoint described in the official docs (a common root is `https://dashscope.aliyuncs.com/api/v1`; follow your environment). Field details: [RerankerConfig](../common/config.md).

**Parameters**:

* **config**(RerankerConfig): Reranker configuration.
* **max_retries**(int): Maximum retry count. Default: 3.
* **retry_wait**(float): Retry wait time in seconds. Default: 0.1.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **verify**(bool | str | ssl.SSLContext): SSL verification settings. Default: True.
* **kwargs**: Extra keyword arguments forwarded to the httpx clients (same as `StandardReranker`).

### async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

Invoke DashScope reranking and return a mapping from document ID to relevance score.

**Parameters**:

* **query**(str | Document): Query text; may also be a `Document` (uses `text`) or [MultimodalDocument](../common/document.md) (uses `dashscope_input` as `input.query`).
* **doc**(list[str | Document]): Candidate list of `str`, `Document`, or `MultimodalDocument`. When any item is multimodal, plain strings are wrapped as `{"text": "..."}` to match the API payload.
* **instruct**(bool | str): Only a **non-empty string** is sent as `parameters.instruct` for a custom task description; `True` or `False` omits that field.
* **kwargs**: Merged into the request `parameters` object (for example you may override `top_n`; if omitted, `top_n` defaults to the number of candidates).

**Returns**:

**dict[str, float]**, mapping from document ID to relevance score.

### rerank_sync

```python
rerank_sync(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

Synchronous variant of `rerank` with the same behavior.

**Parameters**:

* **query**(str | Document): Same as `rerank`.
* **doc**(list[str | Document]): Same as `rerank`.
* **instruct**(bool | str): Same as `rerank`.
* **kwargs**: Same as `rerank`.

**Returns**:

**dict[str, float]**, mapping from document ID to relevance score.
