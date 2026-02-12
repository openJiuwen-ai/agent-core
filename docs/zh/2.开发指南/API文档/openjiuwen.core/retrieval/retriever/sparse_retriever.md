# openjiuwen.core.retrieval.retriever.sparse_retriever

## class openjiuwen.core.retrieval.retriever.sparse_retriever.SparseRetriever

基于BM25的稀疏检索器实现，使用文本匹配进行检索。


```python
SparseRetriever(vector_store: VectorStore, **kwargs: Any)
```

初始化稀疏检索器。

**参数**：

* **vector_store**(VectorStore)：向量存储实例（需要支持稀疏搜索）。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Literal["vector", "sparse", "hybrid"] = "sparse", **kwargs: Any) -> List[RetrievalResult]
```

检索文档（稀疏检索/BM25）。

**参数**：

* **query**(str)：查询字符串。
* **top_k**(int)：返回结果数量。默认值：5。
* **score_threshold**(float, 可选)：得分阈值（稀疏检索不支持）。默认值：None。
* **mode**(Literal["vector", "sparse", "hybrid"])：检索模式（仅支持sparse）。默认值："sparse"。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[RetrievalResult]**，返回检索结果列表，按BM25得分排序。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
>>> from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig
>>> 
>>> async def run():
...     # 创建向量存储
...     vs_config = VectorStoreConfig(collection_name="test_collection")
...     vector_store = ChromaVectorStore(config=vs_config, chroma_path="./chroma_db")
...     # 创建稀疏检索器
...     retriever = SparseRetriever(vector_store=vector_store)
...     results = await retriever.retrieve("测试查询", top_k=5)
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
retrieve_search_results(query: str, top_k: int = 5, mode: Literal["vector", "sparse", "hybrid"] = "sparse", **kwargs: Any) -> List[SearchResult]
```

检索文档。

**参数**：

* **query**(str)：查询字符串。
* **top_k**(int)：返回结果数量。默认值：5。
* **mode**(Literal["vector", "sparse", "hybrid"])：检索模式，vector=向量检索，sparse=稀疏检索/BM25，hybrid=混合检索。默认值："sparse"。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[SearchResult]**，返回搜索结果列表。

### async close

```python
close() -> None
```

关闭检索器并释放资源。

