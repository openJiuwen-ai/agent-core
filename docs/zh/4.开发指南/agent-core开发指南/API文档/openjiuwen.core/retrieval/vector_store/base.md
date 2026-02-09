# openjiuwen.core.retrieval.vector_store.base

## class openjiuwen.core.retrieval.vector_store.base.VectorStore

向量存储抽象基类，提供统一的接口用于向量存储和检索。

### abstractmethod async add

```python
add(data: dict | List[dict], batch_size: int | None = 128, **kwargs: Any) -> None
```

添加向量数据。

**参数**：

* **data**(dict | List[dict])：向量数据，可以是单个字典或字典列表。
* **batch_size**(int, 可选)：批处理大小。默认值：128。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### abstractmethod async search

```python
search(query_vector: List[float], top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

向量搜索。

**参数**：

* **query_vector**(List[float])：查询向量。
* **top_k**(int)：返回结果数量。默认值：5。
* **filters**(dict, 可选)：元数据过滤条件。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### abstractmethod async sparse_search

```python
sparse_search(query_text: str, top_k: int = 5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

稀疏搜索（BM25）。

**参数**：

* **query_text**(str)：查询文本。
* **top_k**(int)：返回结果数量。默认值：5。
* **filters**(dict, 可选)：元数据过滤条件。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### abstractmethod async hybrid_search

```python
hybrid_search(query_text: str, query_vector: Optional[List[float]] = None, top_k: int = 5, alpha: float = 0.5, filters: Optional[dict] = None, **kwargs: Any) -> List[SearchResult]
```

混合搜索（稀疏检索 + 向量检索）。

**参数**：

* **query_text**(str)：查询文本。
* **query_vector**(List[float], 可选)：查询向量（如果提供将直接使用，否则需要先嵌入）。默认值：None。
* **top_k**(int)：返回结果数量。默认值：5。
* **alpha**(float)：混合权重（0=纯稀疏检索，1=纯向量检索，0.5=平衡）。默认值：0.5。
* **filters**(dict, 可选)：元数据过滤条件。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### abstractmethod async delete

```python
delete(ids: Optional[List[str]] = None, filter_expr: Optional[str] = None, **kwargs: Any) -> bool
```

删除向量数据。

**参数**：

* **ids**(List[str], 可选)：要删除的ID列表。默认值：None。
* **filter_expr**(str, 可选)：过滤表达式。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果删除成功则返回True，否则返回False。

