# openjiuwen.core.retrieval.indexing.indexer.base

## class openjiuwen.core.retrieval.indexing.indexer.base.Indexer

索引管理器抽象基类，提供统一的接口用于索引的构建、更新和删除。

### abstractmethod async build_index

```python
build_index(chunks: List[TextChunk], config: IndexConfig, embed_model: Optional[Embedding] = None, **kwargs: Any) -> bool
```

构建索引。

**参数**：

* **chunks**(List[TextChunk])：文本块列表（比如 list）。
* **config**(IndexConfig)：索引配置。
* **embed_model**(Embedding, 可选)：嵌入模型实例（向量索引必需）。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果构建成功则返回True，否则返回False。

### abstractmethod async update_index

```python
update_index(chunks: List[TextChunk], doc_id: str, config: IndexConfig, embed_model: Optional[Embedding] = None, **kwargs: Any) -> bool
```

更新索引。

**参数**：

* **chunks**(List[TextChunk])：文本块列表（比如 list）。
* **doc_id**(str)：文档ID。
* **config**(IndexConfig)：索引配置。
* **embed_model**(Embedding, 可选)：嵌入模型实例（向量索引必需）。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果更新成功则返回True，否则返回False。

### abstractmethod async delete_index

```python
delete_index(doc_id: str, index_name: str, **kwargs: Any) -> bool
```

删除索引。

**参数**：

* **doc_id**(str)：文档ID。
* **index_name**(str)：索引名称。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果删除成功则返回True，否则返回False。

### abstractmethod async index_exists

```python
index_exists(index_name: str) -> bool
```

检查索引是否存在。

**参数**：

* **index_name**(str)：索引名称。

**返回**：

**bool**，如果索引存在则返回True，否则返回False。

### abstractmethod async get_index_info

```python
get_index_info(index_name: str) -> Dict[str, Any]
```

获取索引信息。

**参数**：

* **index_name**(str)：索引名称。

**返回**：

**Dict[str, Any]**，返回索引信息字典。

