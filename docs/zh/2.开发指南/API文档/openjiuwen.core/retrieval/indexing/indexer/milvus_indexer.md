# openjiuwen.core.retrieval.indexing.indexer.milvus_indexer

## class openjiuwen.core.retrieval.indexing.indexer.milvus_indexer.MilvusIndexer

Milvus索引管理器实现，负责构建、更新和删除Milvus索引。


```python
MilvusIndexer(config: VectorStoreConfig, milvus_uri: str, milvus_token: Optional[str] = None, text_field: str = "content", vector_field: str | MilvusVectorField = "embedding", sparse_vector_field: str = "sparse_vector", metadata_field: str = "metadata", doc_id_field: str = "document_id", doc_index_callback: type[BaseCallback] = TqdmCallback, **kwargs: Any)
```

初始化Milvus索引管理器。

**参数**：

* **config**(VectorStoreConfig)：向量存储配置。
* **milvus_uri**(str)：Milvus URI。
* **milvus_token**(str, 可选)：Milvus Token。默认值：None。
* **text_field**(str)：文本字段名。默认值："content"。
* **vector_field**(str | MilvusVectorField)：向量字段名（str）或向量字段配置对象（MilvusVectorField）。默认值："embedding"。更多关于 MilvusVectorField 的配置选项，请参考 [MilvusVectorField 文档](../../../foundation/store/vector_fields/milvus_fields.md)。
* **sparse_vector_field**(str)：稀疏向量字段名。默认值："sparse_vector"。
* **metadata_field**(str)：元数据字段名。默认值："metadata"。
* **doc_id_field**(str)：文档ID字段名。默认值："document_id"。
* **doc_index_callback**(type[BaseCallback])：回调对象类，必须是BaseCallback的子类。默认值：TqdmCallback。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### property client

```python
client -> MilvusClient
```

获取Milvus客户端。

**返回**：

**MilvusClient**，返回Milvus客户端实例。

### async build_index

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

### async update_index

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

### async delete_index

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

### async index_exists

```python
index_exists(index_name: str) -> bool
```

检查索引是否存在。

**参数**：

* **index_name**(str)：索引名称。

**返回**：

**bool**，如果索引存在则返回True，否则返回False。

### async get_index_info

```python
get_index_info(index_name: str) -> Dict[str, Any]
```

获取索引信息。

**参数**：

* **index_name**(str)：索引名称。

**返回**：

**Dict[str, Any]**，返回包含索引统计信息和元数据的字典。

### close

```python
close() -> None
```

关闭索引管理器并释放资源。

