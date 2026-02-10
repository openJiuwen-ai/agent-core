# openjiuwen.core.retrieval.graph_knowledge_base

## class openjiuwen.core.retrieval.graph_knowledge_base.GraphKnowledgeBase

图增强知识库实现，支持图索引和检索，可以从文档中提取三元组并构建图索引，支持基于图的检索扩展。


```python
GraphKnowledgeBase(config: KnowledgeBaseConfig, vector_store: Optional[VectorStore] = None, embed_model: Optional[Embedding] = None, parser: Optional[Parser] = None, chunker: Optional[Chunker] = None, extractor: Optional[Extractor] = None, index_manager: Optional[Indexer] = None, chunk_retriever: Optional[Retriever] = None, triple_retriever: Optional[Retriever] = None, llm_client: Optional[Any] = None, llm_model_name: Optional[Any] = None, **kwargs: Any)
```

初始化图知识库。

**参数**：

* **config**(KnowledgeBaseConfig)：知识库配置，use_graph=True时启用图索引。
* **vector_store**(VectorStore, 可选)：向量存储实例。默认值：None。
* **embed_model**(Embedding, 可选)：嵌入模型实例。默认值：None。
* **parser**(Parser, 可选)：文档解析器实例。默认值：None。
* **chunker**(Chunker, 可选)：文本分块器实例。默认值：None。
* **extractor**(Extractor, 可选)：三元组提取器实例（启用图索引时必需）。默认值：None。
* **index_manager**(Indexer, 可选)：索引管理器实例。默认值：None。
* **chunk_retriever**(Retriever, 可选)：文档块检索器实例。默认值：None。
* **triple_retriever**(Retriever, 可选)：三元组检索器实例。默认值：None。
* **llm_client**(Any, 可选)：LLM客户端实例（用于三元组提取和智能检索）。默认值：None。
* **llm_model_name**(Any, 可选)：LLM模型名称。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async parse_files

```python
parse_files(file_paths: List[str], **kwargs: Any) -> List[Document]
```

从文件路径解析文件，返回Document对象列表。

**参数**：

* **file_paths**(List[str])：文件路径列表。
* **kwargs**(Any)：可变参数，可包含file_name和file_id参数。

**返回**：

**List[Document]**，返回解析后的Document对象列表。

### async add_documents

```python
add_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

向知识库添加文档，包括文档块索引和三元组索引（如果启用图索引）。

**参数**：

* **documents**(List[Document])：文档列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[str]**，返回添加成功的文档ID列表。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig
>>> from openjiuwen.core.retrieval.common.document import Document
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="graph_kb", index_type="hybrid", use_graph=True)
...     # 需要配置chunker、extractor、index_manager和embed_model
...     kb = GraphKnowledgeBase(config=config)
...     documents = [Document(text="北京是中国的首都", id_="doc1")]
...     doc_ids = await kb.add_documents(documents)
...     print(f"Added {len(doc_ids)} documents with graph index")
>>> asyncio.run(run())
Added 1 documents with graph index
```

### async retrieve

```python
retrieve(query: str, config: Optional[RetrievalConfig] = None, **kwargs: Any) -> List[RetrievalResult]
```

检索相关文档，支持图检索。如果config.use_graph或知识库配置的use_graph为True，将使用图检索器进行检索，支持图扩展和多跳检索。

**参数**：

* **query**(str)：查询字符串。
* **config**(RetrievalConfig, 可选)：检索配置，use_graph=True启用图检索，graph_expansion=True启用图扩展。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[RetrievalResult]**，返回检索结果列表，图检索会通过三元组关系进行扩展。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="graph_kb", index_type="hybrid", use_graph=True)
...     # 需要配置vector_store、embed_model、index_manager等
...     kb = GraphKnowledgeBase(config=config)
...     retrieval_config = RetrievalConfig(top_k=5, use_graph=True, graph_expansion=True)
...     results = await kb.retrieve("中国的首都", config=retrieval_config)
...     print(f"Retrieved {len(results)} results with graph expansion")
>>> asyncio.run(run())
Retrieved 5 results with graph expansion
```

### async delete_documents

```python
delete_documents(doc_ids: List[str], **kwargs: Any) -> bool
```

删除文档，包括文档块索引和三元组索引（如果存在）。

**参数**：

* **doc_ids**(List[str])：文档ID列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果所有文档删除成功则返回True，否则返回False。

### async update_documents

```python
update_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

更新文档，包括重新分块、重新提取三元组和更新索引。

**参数**：

* **documents**(List[Document])：文档列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[str]**，返回更新成功的文档ID列表。

### async get_statistics

```python
get_statistics() -> Dict[str, Any]
```

获取知识库统计信息，包括文档块索引和三元组索引信息。

**返回**：

**Dict[str, Any]**，返回包含知识库ID、索引类型、是否启用图索引、文档块索引信息、三元组索引信息等统计信息的字典。

### async close

```python
close() -> None
```

关闭知识库并释放资源，包括图检索器、文档块检索器和三元组检索器。

## func async retrieve_multi_graph_kb

```python
retrieve_multi_graph_kb(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[str]
```

在多个知识库上执行检索（返回文本列表）。

**参数**：

* **kbs**(List[KnowledgeBase])：知识库列表。
* **query**(str)：查询字符串。
* **config**(RetrievalConfig, 可选)：检索配置。默认值：None。
* **top_k**(int, 可选)：返回结果数量。默认值：None。

**返回**：

**List[str]**，返回去重并排序后的文本列表。

## func async retrieve_multi_graph_kb_with_source

```python
retrieve_multi_graph_kb_with_source(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[Dict[str, Any]]
```

在多个知识库上执行检索（包含来源信息）。

**参数**：

* **kbs**(List[KnowledgeBase])：知识库列表。
* **query**(str)：查询字符串。
* **config**(RetrievalConfig, 可选)：检索配置。默认值：None。
* **top_k**(int, 可选)：返回结果数量。默认值：None。

**返回**：

**List[Dict[str, Any]]**，返回包含文本、得分、来源知识库ID等信息的结果列表。

