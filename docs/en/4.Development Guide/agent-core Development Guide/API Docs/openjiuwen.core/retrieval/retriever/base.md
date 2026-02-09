# openjiuwen.core.retrieval.retriever.base

## class openjiuwen.core.retrieval.retriever.base.Retriever

Retriever abstract base class, providing a unified interface for document retrieval.

### abstractmethod async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[RetrievalResult]
```

Retrieve documents.

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Number of results to return. Default: 5.
* **score_threshold**(float, optional): Score threshold, results below this threshold will be filtered. Default: None.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode, vector=vector retrieval, sparse=sparse retrieval/BM25, hybrid=hybrid retrieval. Default: "hybrid".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results.

### abstractmethod async batch_retrieve

```python
batch_retrieve(queries: List[str], top_k: int = 5, **kwargs: Any) -> List[List[RetrievalResult]]
```

Batch retrieval.

**Parameters**:

* **queries**(List[str]): List of query strings.
* **top_k**(int): Number of results to return for each query. Default: 5.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[RetrievalResult]]**, returns a list of retrieval results corresponding to each query.

### async retrieve_search_results

```python
retrieve_search_results(query: str, top_k: int = 5, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[SearchResult]
```

Retrieve documents.

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Number of results to return. Default: 5.
* **mode**(Literal["vector", "sparse", "hybrid"]): Retrieval mode, vector=vector retrieval, sparse=sparse retrieval/BM25, hybrid=hybrid retrieval. Default: "hybrid".
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[SearchResult]**, returns a list of search results.

### async close

```python
close() -> None
```

Close the retriever and release resources.

