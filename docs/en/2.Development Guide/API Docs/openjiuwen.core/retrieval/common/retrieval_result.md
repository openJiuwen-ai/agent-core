# openjiuwen.core.retrieval.common.retrieval_result

## class openjiuwen.core.retrieval.common.retrieval_result.SearchResult

Search result data model, representing search results returned by vector stores.

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `chroma_query_expr.py` - ChromaDB query expression examples
> - `milvus_query_expr.py` - Milvus query expression examples

**Parameters**:

* **id**(str): Result ID.
* **text**(str): Text content.
* **score**(float): Relevance score.
* **metadata**(Dict[str, Any]): Metadata (e.g., `{"source": "doc1", "author": "Alice"}`). Default: {}.

## class openjiuwen.core.retrieval.common.retrieval_result.RetrievalResult

Retrieval result data model, representing retrieval results returned by retrievers.

**Parameters**:

* **text**(str): Text content.
* **score**(float): Relevance score.
* **metadata**(Dict[str, Any]): Metadata (e.g., `{"source": "doc1", "author": "Alice"}`). Default: {}.
* **doc_id**(str, optional): Document ID. Default: None.
* **chunk_id**(str, optional): Text chunk ID. Default: None.

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
>>> 
>>> # Create retrieval result
>>> result = RetrievalResult(
...     text="This is the retrieved text",
...     score=0.95,
...     metadata={"source": "doc1"},
...     doc_id="doc1",
...     chunk_id="chunk1"
... )
>>> print(f"Text: {result.text}, Score: {result.score}, Doc ID: {result.doc_id}")
Text: This is the retrieved text, Score: 0.95, Doc ID: doc1
```

