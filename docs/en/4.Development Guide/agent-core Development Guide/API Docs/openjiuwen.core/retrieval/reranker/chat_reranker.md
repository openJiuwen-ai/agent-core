# openjiuwen.core.retrieval.reranker.chat_reranker

## class openjiuwen.core.retrieval.reranker.chat_reranker.ChatReranker

Chat-based reranker implementation, supports any chat completion API that provide logprobs.

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_reranker.py` - Reranker examples

```python
ChatReranker(config: RerankerConfig, max_retries: int = 3, retry_wait: float = 0.1, extra_headers: Optional[dict] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

Initialize chat reranker.

**Parameters**:

* **config**(RerankerConfig): Reranker configuration, must include `yes_no_ids` field (a sequence of two integers).
* **max_retries**(int): Maximum retry count. Default: 3.
* **retry_wait**(float): Retry wait time in seconds. Default: 0.1.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **verify**(bool | str | ssl.SSLContext): SSL verification settings. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

**Note**:

ChatReranker is an experimental feature and requires the service to support logprobs functionality. The input document list must contain only one document.

### test_compatibility

```python
test_compatibility() -> bool
```

Test to see if selected service is compatible for chat-completion-based reranking.

**Returns**:

**bool**, returns True if the service is compatible, False otherwise.

### async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

Rerank documents and return a mapping from document to relevance score.

**Parameters**:

* **query**(str): Query string.
* **doc**(list[str | Document]): List of documents to rerank (must contain only one document).
* **instruct**(bool | str): Whether to provide instruction to reranker, pass in a string for custom instruction. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

**Returns**:

**dict[str, float]**, returns a mapping from document ID to relevance score.

### rerank_sync

```python
rerank_sync(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

Rerank documents and return a mapping from document to relevance score (synchronous version).

**Parameters**:

* **query**(str): Query string.
* **doc**(list[str | Document]): List of documents to rerank (must contain only one document).
* **instruct**(bool | str): Whether to provide instruction to reranker, pass in a string for custom instruction. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

**Returns**:

**dict[str, float]**, returns a mapping from document ID to relevance score.
