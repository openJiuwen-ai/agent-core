# openjiuwen.core.retrieval.vector_store.base

## class openjiuwen.core.retrieval.vector_store.base.VectorStore

Vector store abstract base class, providing a unified interface for vector storage and retrieval.

### abstractmethod async add

```python
add(data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any) -> None
```

Add vector data.

**Parameters**:

* **data**(dict | List[dict]): Vector data, can be a single dictionary or a list of dictionaries.
* **batch_size**(int, optional): Batch size. Default: 128.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

### abstractmethod async search

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

### abstractmethod async sparse_search

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

### abstractmethod async hybrid_search

```python
hybrid_search(query_text: str, query_vector: Optional[List[float]] = None, top_k: int = 5, alpha: float = 0.5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

Hybrid search (sparse retrieval + vector retrieval).

**Parameters**:

* **query_text**(str): Query text.
* **query_vector**(List[float], optional): Query vector (will be used directly if provided, otherwise needs to be embedded first). Default: None.
* **top_k**(int): Number of results to return. Default: 5.
* **alpha**(float): Hybrid weight (0=pure sparse retrieval, 1=pure vector retrieval, 0.5=balanced). Default: 0.5.
* **filters**(dict, optional): Metadata filter conditions. Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### abstractmethod async delete

```python
delete(ids: List[str], **kwargs: Any) -> bool
```

Delete vectors by IDs.

**Parameters**:

* **ids**(List[str]): List of vector IDs.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**bool**, returns True if deletion is successful, otherwise returns False.

### abstractmethod async close

```python
close() -> None
```

Close the vector store and release resources.

