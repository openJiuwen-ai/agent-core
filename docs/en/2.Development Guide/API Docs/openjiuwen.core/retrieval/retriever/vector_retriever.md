# openjiuwen.core.retrieval.retriever.vector_retriever

## class openjiuwen.core.retrieval.retriever.vector_retriever.VectorRetriever

Retriever implementation based on vector store, using vector similarity for retrieval.


```python
VectorRetriever(vector_store: VectorStore, embed_model: Optional[Embedding] = None, **kwargs: Any)
```

Initialize vector retriever.

**Parameters**:

* **vector_store**(VectorStore): Vector store instance.
* **embed_model**(Embedding, optional): Embedding model instance (required for vector retrieval). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "vector", **kwargs: Any) -> List[RetrievalResult]
```

Retrieve documents (vector retrieval).

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Number of results to return. Default: 5.
* **score_threshold**(float, optional): Score threshold, results below this threshold will be filtered. Default: None.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode (only supports vector). Default: "vector".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results sorted by vector similarity score. If vector retrieval returns no results, it will fall back to BM25 retrieval.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever
>>> from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
>>> from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig, EmbeddingConfig, StoreType
>>> 
>>> async def run():
...     # Create vector store
...     vs_config = VectorStoreConfig(store_provider=StoreType.Chroma, collection_name="test_collection")
...     vector_store = ChromaVectorStore(config=vs_config, chroma_path="./chroma_db")
...     # Create embedding model
...     embed_config = EmbeddingConfig(model_name="text-embedding-ada-002", api_key="your_api_key", base_url="your_base_url")
...     embed_model = APIEmbedding(config=embed_config)
...     # Create vector retriever
...     retriever = VectorRetriever(vector_store=vector_store, embed_model=embed_model)
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
retrieve_search_results(query: str, top_k: int = 5, mode: Literal["vector", "sparse", "hybrid"] = "vector", **kwargs: Any) -> List[SearchResult]
```

Retrieve documents.

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Number of results to return. Default: 5.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode, vector=vector retrieval, sparse=sparse retrieval/BM25, hybrid=hybrid retrieval. Default: "vector".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### async close

```python
close() -> None
```

Close the retriever and release resources.

