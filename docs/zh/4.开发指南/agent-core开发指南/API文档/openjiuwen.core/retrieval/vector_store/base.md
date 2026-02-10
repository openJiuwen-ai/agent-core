# openjiuwen.core.retrieval.vector_store.base

## class openjiuwen.core.retrieval.vector_store.base.VectorStore

向量存储抽象基类，提供统一的接口用于向量存储和检索。

### abstractmethod staticmethod create_client

```python
create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs: Any) -> Any
```

创建向量数据库客户端并确保数据库存在。

**参数**：

* **database_name**(str)：数据库名称。
* **path_or_uri**(str)：路径或URI。
* **token**(str)：访问令牌。默认值：""。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**Any**，返回向量数据库客户端实例。

### abstractmethod check_vector_field

```python
check_vector_field() -> None
```

检查向量字段配置是否与实际数据库一致。

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
search(query_vector: List[float], top_k: int = 5, filters: Optional[dict | QueryExpr] = None, **kwargs: Any) -> List[SearchResult]
```

向量搜索。

**参数**：

* **query_vector**(List[float])：查询向量。
* **top_k**(int)：返回结果数量。默认值：5。
* **filters**(dict | QueryExpr, 可选)：元数据过滤条件。默认值：None。更多关于 QueryExpr 的配置选项，请参考 [QueryExpr 文档](../../foundation/store/query/base.md)。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### abstractmethod async sparse_search

```python
sparse_search(query_text: str, top_k: int = 5, filters: Optional[dict | QueryExpr] = None, **kwargs: Any) -> List[SearchResult]
```

稀疏搜索（BM25）。

**参数**：

* **query_text**(str)：查询文本。
* **top_k**(int)：返回结果数量。默认值：5。
* **filters**(dict | QueryExpr, 可选)：元数据过滤条件。默认值：None。更多关于 QueryExpr 的配置选项，请参考 [QueryExpr 文档](../../foundation/store/query/base.md)。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### abstractmethod async hybrid_search

```python
hybrid_search(query_text: str, query_vector: Optional[List[float]] = None, top_k: int = 5, alpha: float = 0.5, filters: Optional[dict | QueryExpr] = None, **kwargs: Any) -> List[SearchResult]
```

混合搜索（稀疏检索 + 向量检索）。

**参数**：

* **query_text**(str)：查询文本。
* **query_vector**(List[float], 可选)：查询向量（如果提供将直接使用，否则需要先嵌入）。默认值：None。
* **top_k**(int)：返回结果数量。默认值：5。
* **alpha**(float)：混合权重（0=纯稀疏检索，1=纯向量检索，0.5=平衡）。默认值：0.5。
* **filters**(dict | QueryExpr, 可选)：元数据过滤条件。默认值：None。更多关于 QueryExpr 的配置选项，请参考 [QueryExpr 文档](../../foundation/store/query/base.md)。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### abstractmethod async delete

```python
delete(ids: Optional[List[str]] = None, filter_expr: str | QueryExpr | None = None, **kwargs: Any) -> bool
```

删除向量数据。

**参数**：

* **ids**(List[str], 可选)：要删除的ID列表。默认值：None。
* **filter_expr**(str | QueryExpr, 可选)：过滤表达式。默认值：None。更多关于 QueryExpr 的配置选项，请参考 [QueryExpr 文档](../../foundation/store/query/base.md)。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果删除成功则返回True，否则返回False。

### abstractmethod async table_exists

```python
table_exists(table_name: str) -> bool
```

检查集合是否存在于当前数据库中。

**参数**：

* **table_name**(str)：集合名称。

**返回**：

**bool**，如果集合存在则返回True，否则返回False。

### abstractmethod async delete_table

```python
delete_table(table_name: str) -> None
```

从当前数据库中删除集合。

**参数**：

* **table_name**(str)：集合名称。
