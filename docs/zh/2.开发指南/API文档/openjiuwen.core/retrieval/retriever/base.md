# openjiuwen.core.retrieval.retriever.base

## class openjiuwen.core.retrieval.retriever.base.Retriever

检索器抽象基类，提供统一的接口用于文档检索。

### abstractmethod async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[RetrievalResult]
```

检索文档。

**参数**：

* **query**(str)：查询字符串。
* **top_k**(int)：返回结果数量。默认值：5。
* **score_threshold**(float, 可选)：得分阈值，低于此阈值的结果将被过滤。默认值：None。
* **mode**(Literal["vector", "sparse", "hybrid"])：检索模式，vector=向量检索，sparse=稀疏检索/BM25，hybrid=混合检索。默认值："hybrid"。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[RetrievalResult]**，返回检索结果列表。

### abstractmethod async batch_retrieve

```python
batch_retrieve(queries: List[str], top_k: int = 5, **kwargs: Any) -> List[List[RetrievalResult]]
```

批量检索。

**参数**：

* **queries**(List[str])：查询字符串列表。
* **top_k**(int)：每个查询返回的结果数量。默认值：5。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[List[RetrievalResult]]**，返回每个查询对应的检索结果列表。

### async retrieve_search_results

```python
retrieve_search_results(query: str, top_k: int = 5, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[SearchResult]
```

检索文档。

**参数**：

* **query**(str)：查询字符串。
* **top_k**(int)：返回结果数量。默认值：5。
* **mode**(Literal["vector", "sparse", "hybrid"])：检索模式，vector=向量检索，sparse=稀疏检索/BM25，hybrid=混合检索。默认值："hybrid"。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### async close

```python
close() -> None
```

关闭检索器并释放资源。

