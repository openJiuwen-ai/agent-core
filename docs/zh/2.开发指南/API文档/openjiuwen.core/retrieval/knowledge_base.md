# openjiuwen.core.retrieval.knowledge_base

## class openjiuwen.core.retrieval.knowledge_base.KnowledgeBase

知识库抽象基类，提供统一的接口用于知识库的文档解析、索引构建、检索等操作。


```python
KnowledgeBase(config: KnowledgeBaseConfig, vector_store: Optional[VectorStore] = None, embed_model: Optional[Embedding] = None, parser: Optional[Parser] = None, chunker: Optional[Chunker] = None, extractor: Optional[Extractor] = None, index_manager: Optional[Indexer] = None, llm_client: Optional[Any] = None, strict_validation: bool = True, **kwargs: Any)
```

初始化知识库。

**参数**：

* **config**(KnowledgeBaseConfig)：知识库配置。
* **vector_store**(VectorStore, 可选)：向量存储实例。默认值：None。
* **embed_model**(Embedding, 可选)：嵌入模型实例。默认值：None。
* **parser**(Parser, 可选)：文档解析器实例。默认值：None。
* **chunker**(Chunker, 可选)：文本分块器实例。默认值：None。
* **extractor**(Extractor, 可选)：提取器实例（用于提取三元组等）。默认值：None。
* **index_manager**(Indexer, 可选)：索引管理器实例。默认值：None。
* **llm_client**(Any, 可选)：LLM客户端实例（用于图检索等）。默认值：None。
* **strict_validation**(bool, 可选)：是否启用严格验证，启用时会验证向量存储和索引管理器的配置一致性。默认值：True。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### abstractmethod async parse_files

```python
parse_files(file_paths: List[str], **kwargs: Any) -> List[Document]
```

从文件路径或 URL 解析为 Document 列表。当 `parser` 为 `AutoParser` 时，`file_paths` 可同时包含本地文件路径与 URL。

**参数**：

* **file_paths**(List[str])：文件路径或 URL 列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[Document]**，返回解析后的Document对象列表。

### abstractmethod async add_documents

```python
add_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

向知识库添加文档。

**参数**：

* **documents**(List[Document])：文档列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[str]**，返回添加成功的文档ID列表。

### abstractmethod async retrieve

```python
retrieve(query: str, config: Optional[RetrievalConfig] = None, **kwargs: Any) -> List[RetrievalResult]
```

检索相关文档。

**参数**：

* **query**(str)：查询字符串。
* **config**(RetrievalConfig, 可选)：检索配置。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[RetrievalResult]**，返回检索结果列表。

### abstractmethod async delete_documents

```python
delete_documents(doc_ids: List[str], **kwargs: Any) -> bool
```

删除文档。

**参数**：

* **doc_ids**(List[str])：文档ID列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果删除成功则返回True，否则返回False。

### abstractmethod async update_documents

```python
update_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

更新文档。

**参数**：

* **documents**(List[Document])：文档列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[str]**，返回更新成功的文档ID列表。

### abstractmethod async get_statistics

```python
get_statistics() -> Dict[str, Any]
```

获取知识库统计信息。

**返回**：

**Dict[str, Any]**，返回知识库的统计信息字典。

### async delete_collection

```python
delete_collection(collection: str) -> None
```

删除当前数据库中的集合。

**参数**：

* **collection**(str)：要删除的集合名称。

### async close

```python
close() -> None
```

关闭知识库并释放资源。

