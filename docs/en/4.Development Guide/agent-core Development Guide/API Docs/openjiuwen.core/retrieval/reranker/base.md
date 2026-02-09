# openjiuwen.core.retrieval.reranker.base

## class openjiuwen.core.retrieval.reranker.base.Reranker

Reranker abstract base class, providing a unified interface for document reranking.

### abstractmethod async rerank

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

### abstractmethod rerank_sync

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
