# openjiuwen.core.retrieval.retriever.sparse_retriever

## class openjiuwen.core.retrieval.retriever.sparse_retriever.SparseRetriever

Sparse retriever implementation based on BM25, using text matching for retrieval.


```python
SparseRetriever(vector_store: VectorStore, **kwargs: Any)
```

Initialize sparse retriever.

**Parameters**:

* **vector_store**(VectorStore): Vector store instance (needs to support sparse search).
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "sparse", **kwargs: Any) -> List[RetrievalResult]
```

Retrieve documents (sparse retrieval/BM25).

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Number of results to return. Default: 5.
* **score_threshold**(float, optional): Score threshold (not supported for sparse retrieval). Default: None.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode (only supports sparse). Default: "sparse".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results sorted by BM25 score.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
>>> from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig, StoreType
>>> 
>>> async def run():
...     # Create vector store
...     vs_config = VectorStoreConfig(store_provider=StoreType.Chroma, collection_name="test_collection")
...     vector_store = ChromaVectorStore(config=vs_config, chroma_path="./chroma_db")
...     # Create sparse retriever
...     retriever = SparseRetriever(vector_store=vector_store)
...     results = await retriever.retrieve("test query", top_k=5)
...     print(f"Retrieved {len(results)} results")
>>> asyncio.run(run())
Retrieved 5 results
```

### async batch_retrieve

```python
batch_retrieve(queries: List[str], top_k: int = 5, **kwargs: Any) -> List[List[RetrievalResult]]
```

Batch retrieval, concurrently executing multiple queries.

**Parameters**:

* **queries**(List[str]): List of query strings.
* **top_k**(int): Number of results to return for each query. Default: 5.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[RetrievalResult]]**, returns a list of retrieval results corresponding to each query.

### async retrieve_search_results

```python
retrieve_search_results(query: str, top_k: int = 5, mode: Literal["vector", "sparse", "hybrid"] = "sparse", **kwargs: Any) -> List[SearchResult]
```

Retrieve documents.

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Number of results to return. Default: 5.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode, vector=vector retrieval, sparse=sparse retrieval/BM25, hybrid=hybrid retrieval. Default: "sparse".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### async close

```python
close() -> None
```

Close the retriever and release resources.

