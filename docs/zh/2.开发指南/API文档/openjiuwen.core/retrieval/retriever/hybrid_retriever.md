# openjiuwen.core.retrieval.retriever.hybrid_retriever

## class openjiuwen.core.retrieval.retriever.hybrid_retriever.HybridRetriever

混合检索器实现，结合向量检索和稀疏检索（BM25），使用RRF（Reciprocal Rank Fusion）算法融合结果。


```python
HybridRetriever(vector_store: VectorStore, embed_model: Optional[Embedding] = None, alpha: float = 0.5, **kwargs: Any)
```

初始化混合检索器。

**参数**：

* **vector_store**(VectorStore)：向量存储实例。
* **embed_model**(Embedding, 可选)：嵌入模型实例（向量检索必需）。默认值：None。
* **alpha**(float)：混合权重（0=纯稀疏检索，1=纯向量检索，0.5=平衡）。默认值：0.5。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "hybrid", **kwargs: Any) -> List[RetrievalResult]
```

检索文档（混合检索）。

**参数**：

* **query**(str)：查询字符串。
* **top_k**(int)：返回结果数量。默认值：5。
* **score_threshold**(float, 可选)：得分阈值（仅支持vector模式）。默认值：None。
* **mode**(Literal["vector", "sparse", "hybrid"])：检索模式，支持hybrid、vector和sparse。默认值："hybrid"。
* **kwargs**(Any)：可变参数，可包含alpha参数用于覆盖默认混合权重。

**返回**：

**List[RetrievalResult]**，返回检索结果列表，使用RRF算法融合向量检索和稀疏检索的结果。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever
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
...     # 创建混合检索器
...     retriever = HybridRetriever(vector_store=vector_store, embed_model=embed_model, alpha=0.5)
...     results = await retriever.retrieve("测试查询", top_k=5, mode="hybrid")
...     print(f"Retrieved {len(results)} results")
>>> asyncio.run(run())
Retrieved 5 results
```

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

