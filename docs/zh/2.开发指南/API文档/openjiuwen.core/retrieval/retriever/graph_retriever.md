# openjiuwen.core.retrieval.retriever.graph_retriever

## class openjiuwen.core.retrieval.retriever.graph_retriever.GraphRetriever

图检索器实现，结合文档块检索和图检索，支持基于三元组关系的图扩展和多跳检索。


```python
GraphRetriever(chunk_retriever: Optional[Retriever] = None, triple_retriever: Optional[Retriever] = None, vector_store: Optional[Any] = None, embed_model: Optional[Any] = None, chunk_collection: Optional[str] = None, triple_collection: Optional[str] = None, **kwargs: Any)
```

初始化图检索器。

**参数**：

* **chunk_retriever**(Retriever, 可选)：文档块检索器（用于文档块检索，可选，如果未提供将根据mode动态创建）。默认值：None。
* **triple_retriever**(Retriever, 可选)：三元组检索器（用于三元组检索，可选，如果未提供将根据mode动态创建）。默认值：None。
* **vector_store**(Any, 可选)：向量存储实例（用于动态创建检索器）。默认值：None。
* **embed_model**(Any, 可选)：嵌入模型实例（用于动态创建检索器）。默认值：None。
* **chunk_collection**(str, 可选)：文档块集合名称（用于动态创建检索器）。默认值：None。
* **triple_collection**(str, 可选)：三元组集合名称（用于动态创建检索器）。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### get_retriever_for_mode

```python
get_retriever_for_mode(mode: Literal["vector", "sparse", "hybrid"], is_chunk: bool = True) -> Retriever
```

返回指定模式对应的文档块检索器或三元组检索器。

**参数**：

* **mode**(Literal["vector", "sparse", "hybrid"])：目标检索模式。
* **is_chunk**(bool)：是否返回文档块检索器（`True`）或三元组检索器（`False`）。默认值：True。

**返回**：

**Retriever**，可用于指定模式检索的检索器实例。

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[RetrievalResult]
```

检索文档（图检索），支持图扩展和多跳检索。

**参数**：

* **query**(str)：查询字符串。
* **top_k**(int)：返回结果数量。默认值：5。
* **score_threshold**(float, 可选)：得分阈值（仅支持vector模式）。默认值：None。
* **mode**(Literal["vector", "sparse", "hybrid"])：检索模式（必须与index_type兼容）。默认值："hybrid"。
* **kwargs**(Any)：可变参数，可包含topk_triples（三元组检索数量）和graph_hops（图扩展跳数）参数。

**返回**：

**List[RetrievalResult]**，返回检索结果列表，通过三元组关系进行图扩展，支持多跳检索。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
>>> from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
>>> from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig, EmbeddingConfig
>>> 
>>> async def run():
...     # 创建向量存储
...     vs_config = VectorStoreConfig(collection_name="test_collection")
...     vector_store = ChromaVectorStore(config=vs_config, chroma_path="./chroma_db")
...     # 创建嵌入模型
...     embed_config = EmbeddingConfig(model_name="text-embedding-ada-002", api_key="your_api_key", base_url="your_base_url")
...     embed_model = APIEmbedding(config=embed_config)
...     # 创建图检索器
...     retriever = GraphRetriever(
...         vector_store=vector_store,
...         embed_model=embed_model,
...         chunk_collection="kb_test_chunks",
...         triple_collection="kb_test_triples"
...     )
...     retriever.index_type = "hybrid"  # 设置索引类型
...     results = await retriever.retrieve("测试查询", top_k=5, graph_hops=2)
...     print(f"Retrieved {len(results)} results with graph expansion")
>>> asyncio.run(run())
Retrieved 5 results with graph expansion
```

### async graph_expansion

```python
graph_expansion(query: str, chunks: List[RetrievalResult], triples: Optional[List[RetrievalResult]] = None, topk: Optional[int] = None, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[RetrievalResult]
```

图扩展：基于初始文档块检索结果，通过三元组进行扩展检索。

**参数**：

* **query**(str)：查询字符串。
* **chunks**(List[RetrievalResult])：初始文档块检索结果。
* **triples**(List[RetrievalResult], 可选)：预先获取的三元组结果（可选）。默认值：None。
* **topk**(int, 可选)：最终返回数量。默认值：None。
* **mode**(Literal["vector", "sparse", "hybrid"])：检索模式。默认值："hybrid"。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[RetrievalResult]**，返回扩展后的检索结果列表。

### async batch_retrieve

```python
batch_retrieve(queries: List[str], top_k: int = 5, **kwargs: Any) -> List[List[RetrievalResult]]
```

批量检索，并发执行多个查询。

**参数**：

* **queries**(List[str])：查询字符串列表。
* **top_k**(int)：每个查询返回的结果数量。默认值：5。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[List[RetrievalResult]]**，返回每个查询对应的检索结果列表。

### async close

```python
close() -> None
```

关闭检索器并释放资源，包括文档块检索器和三元组检索器。

