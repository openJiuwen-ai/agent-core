# openjiuwen.core.retrieval.retriever.graph_retriever

## class openjiuwen.core.retrieval.retriever.graph_retriever.GraphRetriever

Graph retriever implementation that combines document chunk retrieval and graph retrieval, supporting graph expansion and multi-hop retrieval based on triple relationships.


```python
GraphRetriever(chunk_retriever: Optional[Retriever] = None, triple_retriever: Optional[Retriever] = None, vector_store: Optional[Any] = None, embed_model: Optional[Any] = None, chunk_collection: Optional[str] = None, triple_collection: Optional[str] = None, **kwargs: Any)
```

Initialize graph retriever.

**Parameters**:

* **chunk_retriever**(Retriever, optional): Document chunk retriever (for document chunk retrieval, optional, will be dynamically created based on mode if not provided). Default: None.
* **triple_retriever**(Retriever, optional): Triple retriever (for triple retrieval, optional, will be dynamically created based on mode if not provided). Default: None.
* **vector_store**(Any, optional): Vector store instance (for dynamically creating retrievers). Default: None.
* **embed_model**(Any, optional): Embedding model instance (for dynamically creating retrievers). Default: None.
* **chunk_collection**(str, optional): Document chunk collection name (for dynamically creating retrievers). Default: None.
* **triple_collection**(str, optional): Triple collection name (for dynamically creating retrievers). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### get_retriever_for_mode

```python
get_retriever_for_mode(mode: Literal["vector", "sparse", "hybrid"], is_chunk: bool = True) -> Retriever
```

Return either the chunk retriever or triple retriever.

**Parameters**:

* **mode**(Literal["vector", "sparse", "hybrid"]): Target retrieval mode.
* **is_chunk**(bool): Whether to resolve the chunk retriever (`True`) or the triple retriever (`False`). Default: True.

**Returns**:

**Retriever**, retriever instance ready to execute queries under the requested mode.

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[RetrievalResult]
```

Retrieve documents (graph retrieval), supporting graph expansion and multi-hop retrieval.

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Number of results to return. Default: 5.
* **score_threshold**(float, optional): Score threshold (only supports vector mode). Default: None.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode (must be compatible with index_type). Default: "hybrid".
* **kwargs**(Any): Variable arguments that may include topk_triples (number of triple retrievals) and graph_hops (number of graph expansion hops) parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results, expanding through triple relationships for graph expansion, supporting multi-hop retrieval.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
>>> from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
>>> from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig, EmbeddingConfig
>>> 
>>> async def run():
...     # Create vector store
...     vs_config = VectorStoreConfig(collection_name="test_collection")
...     vector_store = ChromaVectorStore(config=vs_config, chroma_path="./chroma_db")
...     # Create embedding model
...     embed_config = EmbeddingConfig(model_name="text-embedding-ada-002", api_key="your_api_key", base_url="your_base_url")
...     embed_model = APIEmbedding(config=embed_config)
...     # Create graph retriever
...     retriever = GraphRetriever(
...         vector_store=vector_store,
...         embed_model=embed_model,
...         chunk_collection="kb_test_chunks",
...         triple_collection="kb_test_triples"
...     )
...     retriever.index_type = "hybrid"  # Set index type
...     results = await retriever.retrieve("test query", top_k=5, graph_hops=2)
...     print(f"Retrieved {len(results)} results with graph expansion")
>>> asyncio.run(run())
Retrieved 5 results with graph expansion
```

### async graph_expansion

```python
graph_expansion(query: str, chunks: List[RetrievalResult], triples: Optional[List[RetrievalResult]] = None, topk: Optional[int] = None, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[RetrievalResult]
```

Graph expansion: Based on initial document chunk retrieval results, expand retrieval through triples.

**Parameters**:

* **query**(str): Query string.
* **chunks**(List[RetrievalResult]): Initial document chunk retrieval results.
* **triples**(List[RetrievalResult], optional): Optional pre-fetched triples. Default: None.
* **topk**(int, optional): Final number of results to return. Default: None.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode. Default: "hybrid".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of expanded retrieval results.

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

### async close

```python
close() -> None
```

Close the retriever and release resources.

