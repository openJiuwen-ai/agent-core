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

## class openjiuwen.core.retrieval.common.retrieval_result.MultiKBRetrievalResult

Multi-knowledge-base retrieval result data model, used when retrieving across multiple knowledge bases. Returned by `retrieve_multi_kb_with_source`.

**Parameters**:

* **text**(str): Text content.
* **score**(float): Relevance score (merged across knowledge bases, highest score is kept).
* **raw_score**(float): Raw relevance score before merging.
* **raw_score_scaled**(float): Scaled raw relevance score between 0 and 1.
* **kb_ids**(list): List of knowledge base IDs where this result was found.
* **metadata**(Dict[str, Any]): Metadata. Default: {}.

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.retrieval_result import MultiKBRetrievalResult
>>> 
>>> result = MultiKBRetrievalResult(
...     text="Relevant document text",
...     score=0.92,
...     raw_score=0.88,
...     raw_score_scaled=0.90,
...     kb_ids=["kb_1", "kb_2"],
...     metadata={"source": "doc1"},
... )
>>> print(f"Text: {result.text}, Score: {result.score}, KBs: {result.kb_ids}")
Text: Relevant document text, Score: 0.92, KBs: ['kb_1', 'kb_2']
```

