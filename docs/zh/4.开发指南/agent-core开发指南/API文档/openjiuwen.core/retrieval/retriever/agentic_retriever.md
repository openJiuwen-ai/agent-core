# openjiuwen.core.retrieval.retriever.agentic_retriever

## class openjiuwen.core.retrieval.retriever.agentic_retriever.AgenticRetriever

Agentic 检索器，在图检索的基础上增加LLM查询重写和多轮融合能力，通过多轮检索和查询优化提升检索效果。


```python
AgenticRetriever(graph_retriever: GraphRetriever, llm_client: Any, llm_model_name: Optional[str] = None, max_iter: int = 3, agent_topk: int = 15)
```

初始化Agentic 检索器。

**参数**：

* **graph_retriever**(GraphRetriever)：图检索器实例。
* **llm_client**(Any)：LLM客户端实例（用于查询重写）。
* **llm_model_name**(str, 可选)：LLM模型名称。默认值：None。
* **max_iter**(int)：最大迭代轮数。默认值：3。
* **agent_topk**(int)：每轮检索返回的结果数量。默认值：15。

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
>>> from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
>>> 
>>> async def run():
...     # 创建图检索器
...     graph_retriever = GraphRetriever(...)
...     # 创建LLM客户端
...     llm_client = OpenAIModelClient(...)
...     # 创建Agentic 检索器
...     retriever = AgenticRetriever(
...         graph_retriever=graph_retriever,
...         llm_client=llm_client,
...         llm_model_name="<model_name>",
...         max_iter=3,
...         agent_topk=15
...     )
...     results = await retriever.retrieve("测试查询", top_k=5)
...     print(f"Retrieved {len(results)} results with agentic retrieval")
>>> asyncio.run(run())
Retrieved 5 results with agentic retrieval
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

