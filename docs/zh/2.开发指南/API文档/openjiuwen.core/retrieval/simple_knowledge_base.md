# openjiuwen.core.retrieval.simple_knowledge_base

## class openjiuwen.core.retrieval.simple_knowledge_base.SimpleKnowledgeBase

基于向量检索的知识库实现，提供完整的知识库功能，包括文档解析、分块、索引构建和检索。

> **参考实现**：基于 `SimpleKnowledgeBase` 的示例项目。该项目实现了一个持续更新的 PEP（Python Enhancement Proposals）知识库，用于为代码的审视与修复提供风格与规范层面的专业建议。详见 [TomatoReviewer](https://gitcode.com/SushiNinja/TomatoReviewer)。

```python
SimpleKnowledgeBase(config: KnowledgeBaseConfig, vector_store: Optional[VectorStore] = None, embed_model: Optional[Embedding] = None, parser: Optional[Parser] = None, chunker: Optional[Chunker] = None, extractor: Optional[Extractor] = None, index_manager: Optional[Indexer] = None, retriever: Optional[Retriever] = None, llm_client: Optional[BaseModelClient] = None, **kwargs: Any)
```

初始化简单知识库。

**参数**：

* **config**(KnowledgeBaseConfig)：知识库配置。
* **vector_store**(VectorStore, 可选)：向量存储实例。默认值：None。
* **embed_model**(Embedding, 可选)：嵌入模型实例。默认值：None。
* **parser**(Parser, 可选)：文档解析器实例。默认值：None。
* **chunker**(Chunker, 可选)：文本分块器实例。默认值：None。
* **extractor**(Extractor, 可选)：提取器实例。默认值：None。
* **index_manager**(Indexer, 可选)：索引管理器实例。默认值：None。
* **retriever**(Retriever, 可选)：检索器实例（可选，如果未提供将根据index_type自动创建）。默认值：None。
* **llm_client**(BaseModelClient, 可选)：LLM客户端实例（智能检索时必需）。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async parse_files

```python
parse_files(file_paths: List[str], **kwargs: Any) -> List[Document]
```

从文件路径或 URL 解析为 Document 列表。当 `parser` 为 `AutoParser` 时，`file_paths` 可同时包含本地文件路径与 URL。

**参数**：

* **file_paths**(List[str])：文件路径或 URL 列表。
* **kwargs**(Any)：可变参数，可包含file_name和file_id参数。

**返回**：

**List[Document]**，返回解析后的Document对象列表。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig
>>> from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import TxtMdParser
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="test_kb", index_type="vector")
...     parser = TxtMdParser()
...     kb = SimpleKnowledgeBase(config=config, parser=parser)
...     documents = await kb.parse_files(["test.txt"])
...     print(f"Parsed {len(documents)} documents")
>>> asyncio.run(run())
Parsed 1 documents
```

### async add_documents

```python
add_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

向知识库添加文档，包括文档分块和索引构建。

**参数**：

* **documents**(List[Document])：文档列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[str]**，返回添加成功的文档ID列表。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig
>>> from openjiuwen.core.retrieval.common.document import Document
>>> from openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker import CharChunker
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="test_kb", index_type="vector")
...     chunker = CharChunker(chunk_size=512, chunk_overlap=50)
...     # 需要配置index_manager和embed_model
...     kb = SimpleKnowledgeBase(config=config, chunker=chunker)
...     documents = [Document(text="这是一个测试文档", id_="doc1")]
...     doc_ids = await kb.add_documents(documents)
...     print(f"Added {len(doc_ids)} documents")
>>> asyncio.run(run())
Added 1 documents
```

### async retrieve

```python
retrieve(query: str, config: Optional[RetrievalConfig] = None, **kwargs: Any) -> List[RetrievalResult]
```

检索相关文档。如果未提供retriever，将根据index_type自动创建相应的检索器。当 `config.agentic` 为 True 时，`AgenticRetriever` 会包装底层检索器执行迭代查询重写和结果融合（需要配置 `llm_client`）。

**参数**：

* **query**(str)：查询字符串。
* **config**(RetrievalConfig, 可选)：检索配置。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[RetrievalResult]**，返回检索结果列表，按相关性得分排序。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
>>> from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
>>> 
>>> async def run():
...     config = KnowledgeBaseConfig(kb_id="test_kb", index_type="vector")
...     # 需要配置vector_store、embed_model和index_manager
...     kb = SimpleKnowledgeBase(config=config)
...     results = await kb.retrieve("测试查询", config=RetrievalConfig(top_k=5))
...     print(f"Retrieved {len(results)} results")
>>> asyncio.run(run())
Retrieved 5 results
```

### async delete_documents

```python
delete_documents(doc_ids: List[str], **kwargs: Any) -> bool
```

删除文档。

**参数**：

* **doc_ids**(List[str])：文档ID列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**bool**，如果所有文档删除成功则返回True，否则返回False。

### async update_documents

```python
update_documents(documents: List[Document], **kwargs: Any) -> List[str]
```

更新文档，包括重新分块和更新索引。

**参数**：

* **documents**(List[Document])：文档列表。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[str]**，返回更新成功的文档ID列表。

### async get_statistics

```python
get_statistics() -> Dict[str, Any]
```

获取知识库统计信息。

**返回**：

**Dict[str, Any]**，返回包含知识库ID、索引类型、索引信息等统计信息的字典。

## func async retrieve_multi_kb

```python
retrieve_multi_kb(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[str]
```

在多个知识库上执行检索，按文本去重并按得分降序合并结果。

**参数**：

* **kbs**(List[KnowledgeBase])：知识库列表。
* **query**(str)：查询字符串。
* **config**(RetrievalConfig, 可选)：检索配置。默认值：None。
* **top_k**(int, 可选)：返回结果数量。默认值：None。

**返回**：

**List[str]**，返回去重并排序后的文本列表。

## func async retrieve_multi_kb_with_source

```python
retrieve_multi_kb_with_source(kbs: List[KnowledgeBase], query: str, config: Optional[RetrievalConfig] = None, top_k: Optional[int] = None) -> List[MultiKBRetrievalResult]
```

在多个知识库上执行检索，返回包含来源信息的结果。每个结果项为 `MultiKBRetrievalResult` 对象，包含：text、score、raw_score、raw_score_scaled、kb_ids 和 metadata。

**参数**：

* **kbs**(List[KnowledgeBase])：知识库列表。
* **query**(str)：查询字符串。
* **config**(RetrievalConfig, 可选)：检索配置。默认值：None。
* **top_k**(int, 可选)：返回结果数量。默认值：None。

**返回**：

**List[MultiKBRetrievalResult]**，返回 `MultiKBRetrievalResult` 对象列表，包含文本、得分、原始得分、来源知识库ID和元数据等信息。

