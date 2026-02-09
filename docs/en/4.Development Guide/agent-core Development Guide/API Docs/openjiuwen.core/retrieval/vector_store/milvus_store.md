# openjiuwen.core.retrieval.vector_store.milvus_store

## class openjiuwen.core.retrieval.vector_store.milvus_store.MilvusVectorStore

Milvus vector store implementation, supporting vector search, sparse search (BM25), and hybrid search.

> **Reference**: Vector database similarity score calculation: [VectorStoreScoring](https://gitcode.com/SushiNinja/VectorStoreScoring).


```python
MilvusVectorStore(config: VectorStoreConfig, milvus_uri: str, milvus_token: Optional[str] = None, text_field: str = "content", vector_field: str = "embedding", sparse_vector_field: str = "sparse_vector", metadata_field: str = "metadata", doc_id_field: str = "document_id", **kwargs: Any)
```

Initialize Milvus vector store.

**Parameters**:

* **config**(VectorStoreConfig): Vector store configuration.
* **milvus_uri**(str): Milvus URI.
* **milvus_token**(str, optional): Milvus Token. Default: None.
* **text_field**(str): Text field name. Default: "content".
* **vector_field**(str): Vector field name. Default: "embedding".
* **sparse_vector_field**(str): Sparse vector field name. Default: "sparse_vector".
* **metadata_field**(str): Metadata field name. Default: "metadata".
* **doc_id_field**(str): Document ID field name. Default: "document_id".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### property client

```python
client() -> MilvusClient
```

Get Milvus client.

**Returns**:

**MilvusClient**, returns Milvus client instance.

### async add

```python
add(data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any) -> None
```

Add vector data.

**Parameters**:

* **data**(dict | List[dict]): Vector data, can be a single dictionary or a list of dictionaries.
* **batch_size**(int, optional): Batch size. Default: 128.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### async search

```python
search(query_vector: List[float], top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

Vector search.

**Parameters**:

* **query_vector**(List[float]): Query vector.
* **top_k**(int): Number of results to return. Default: 5.
* **filters**(dict, optional): Metadata filter conditions. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### async sparse_search

```python
sparse_search(query_text: str, top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

Sparse search (BM25).

**Parameters**:

* **query_text**(str): Query text.
* **top_k**(int): Number of results to return. Default: 5.
* **filters**(dict, optional): Metadata filter conditions. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### async hybrid_search

```python
hybrid_search(query_text: str, query_vector: Optional[List[float]] = None, top_k: int = 5, alpha: float = 0.5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

Hybrid search (sparse retrieval + vector retrieval), supporting native hybrid search and RRF ranking.

**Parameters**:

* **query_text**(str): Query text.
* **query_vector**(List[float], optional): Query vector (will be used directly if provided, otherwise needs to be embedded first). Default: None.
* **top_k**(int): Number of results to return. Default: 5.
* **alpha**(float): Hybrid weight (0=pure sparse retrieval, 1=pure vector retrieval, 0.5=balanced). Default: 0.5.
* **filters**(dict, optional): Metadata filter conditions. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results, using Milvus native hybrid search and RRF ranking.

### async delete

```python
delete(ids: Optional[List[str]] = None, filter_expr: Optional[str] = None, **kwargs: Any) -> bool
```

Delete vector data.

**Parameters**:

* **ids**(List[str], optional): List of IDs to delete. Default: None.
* **filter_expr**(str, optional): Filter expression. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if deletion is successful, otherwise returns False.

### close

```python
close() -> None
```

Close the vector store and release resources.

