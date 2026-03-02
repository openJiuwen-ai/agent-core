# openjiuwen.core.retrieval.retriever.agentic_retriever

## class openjiuwen.core.retrieval.retriever.agentic_retriever.AgenticRetriever

Agentic 检索器，在任意检索器的基础上增加LLM查询重写和多轮融合能力，通过迭代检索和查询优化提升检索效果。

当底层检索器为 `GraphRetriever` 时，图扩展和三元组链接等图特有功能会自动启用。对于其他 `Retriever` 子类，智能体直接执行迭代查询重写和结果融合。


```python
AgenticRetriever(retriever: Retriever, llm_client: BaseModelClient, max_iter: int = 2)
```

初始化Agentic 检索器。

**参数**：

* **retriever**(Retriever)：底层检索器实例。接受任意 `Retriever` 子类。当提供 `GraphRetriever` 时，图特有功能会自动启用。
* **llm_client**(BaseModelClient)：LLM客户端实例（用于三元组提取和查询重写）。
* **max_iter**(int)：最大迭代轮数。默认值：2。

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Optional[Literal["vector", "sparse", "hybrid"]] = None, **kwargs: Any) -> List[RetrievalResult]
```

检索文档（智能检索），通过多轮检索和查询重写优化检索效果。

**参数**：

* **query**(str)：查询字符串。
* **top_k**(int)：最终返回结果数量。默认值：5。
* **score_threshold**(float, 可选)：得分阈值。默认值：None。
* **mode**(Literal["vector", "sparse", "hybrid"], 可选)：检索模式（如果未提供将根据index_type自动选择）。默认值：None。
* **kwargs**(Any)：可变参数，用于传递其他额外的配置参数。

**返回**：

**List[RetrievalResult]**，返回检索结果列表，通过多轮检索和RRF融合得到最终结果。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.agentic_retriever import AgenticRetriever
>>> from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
>>> from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever
>>> from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
>>> 
>>> async def run():
...     # 创建LLM客户端
...     llm_client = OpenAIModelClient(...)
...
...     # 示例1：使用GraphRetriever（自动启用图特有功能）
...     graph_retriever = GraphRetriever(...)
...     agentic = AgenticRetriever(
...         retriever=graph_retriever,
...         llm_client=llm_client,
...         max_iter=2,
...     )
...     results = await agentic.retrieve("测试查询", top_k=5)
...     print(f"Retrieved {len(results)} results with graph-agentic retrieval")
...
...     # 示例2：使用其他检索器（如VectorRetriever）
...     vector_retriever = VectorRetriever(...)
...     agentic = AgenticRetriever(
...         retriever=vector_retriever,
...         llm_client=llm_client,
...         max_iter=2,
...     )
...     results = await agentic.retrieve("测试查询", top_k=5)
...     print(f"Retrieved {len(results)} results with generic agentic retrieval")
>>> asyncio.run(run())
Retrieved 5 results with graph-agentic retrieval
Retrieved 5 results with generic agentic retrieval
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

### async close

```python
close() -> None
```

关闭检索器并释放资源。

