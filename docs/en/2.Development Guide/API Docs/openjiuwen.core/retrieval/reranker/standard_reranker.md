# openjiuwen.core.retrieval.reranker.standard_reranker

## class openjiuwen.core.retrieval.reranker.standard_reranker.StandardReranker

Standard reranker implementation, supports rerank API of vLLM-like services.

```python
StandardReranker(config: RerankerConfig, max_retries: int = 3, retry_wait: float = 0.1, extra_headers: Optional[dict] = None, verify: bool | str | ssl.SSLContext = True, **kwargs)
```

Initialize standard reranker.

**Parameters**:

* **config**(RerankerConfig): Reranker configuration.
* **max_retries**(int): Maximum retry count. Default: 3.
* **retry_wait**(float): Retry wait time in seconds. Default: 0.1.
* **extra_headers**(dict, optional): Additional request headers. Default: None.
* **verify**(bool | str | ssl.SSLContext): SSL verification settings. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

### async rerank

```python
rerank(query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs) -> dict[str, float]
```

Rerank documents and return a mapping from document to relevance score.

**Parameters**:

* **query**(str): Query string.
* **doc**(list[str | Document]): List of documents to rerank.
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
* **doc**(list[str | Document]): List of documents to rerank.
* **instruct**(bool | str): Whether to provide instruction to reranker, pass in a string for custom instruction. Default: True.
* **kwargs**: Variable arguments for passing additional configuration parameters.

**Returns**:

**dict[str, float]**, returns a mapping from document ID to relevance score.

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `showcase_reranker.py` - Reranker examples
