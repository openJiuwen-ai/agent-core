# openjiuwen.core.retrieval.retriever.agentic_retriever

## class openjiuwen.core.retrieval.retriever.agentic_retriever.AgenticRetriever

Agentic retriever that adds LLM query rewriting and multi-round fusion capabilities on top of graph retrieval, improving retrieval effectiveness through multi-round retrieval and query optimization.


```python
__init__(graph_retriever: GraphRetriever, llm_client: Any, llm_model_name: Optional[str] = None, max_iter: int = 2, agent_topk: int = 15)
```

Initialize agentic retriever.

**Parameters**:

* **graph_retriever**(GraphRetriever): Graph retriever instance.
* **llm_client**(Any): LLM client instance (for query rewriting).
* **llm_model_name**(str, optional): LLM model name. Default: None.
* **max_iter**(int): Maximum number of iterations. Default: 2.
* **agent_topk**(int): Number of results to return per round of retrieval. Default: 15.

### async retrieve

```python
retrieve(query: str, top_k: int = 5, score_threshold: Optional[float] = None, mode: Optional[Literal["vector", "sparse", "hybrid"]] = None, **kwargs: Any) -> List[RetrievalResult]
```

Retrieve documents (agentic retrieval), optimizing retrieval effectiveness through multi-round retrieval and query rewriting.

**Parameters**:

* **query**(str): Query string.
* **top_k**(int): Final number of results to return. Default: 5.
* **score_threshold**(float, optional): Score threshold. Default: None.
* **mode**(Literal["vector", "sparse", "hybrid"], optional): Retrieval mode (will be automatically selected based on index_type if not provided). Default: None.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[RetrievalResult]**, returns a list of retrieval results, obtained through multi-round retrieval and RRF fusion.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.core.retrieval.retriever.agentic_retriever import AgenticRetriever
>>> from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
>>> from openjiuwen.core.utils.llm.base import BaseModelClient
>>> 
>>> async def run():
...     # Create graph retriever
...     graph_retriever = GraphRetriever(...)
...     # Create LLM client
...     llm_client = BaseModelClient(...)
...     # Create agentic retriever
...     retriever = AgenticRetriever(
...         graph_retriever=graph_retriever,
...         llm_client=llm_client,
...         llm_model_name="gpt-4",
...         max_iter=3,
...         agent_topk=15
...     )
...     results = await retriever.retrieve("test query", top_k=5)
...     print(f"Retrieved {len(results)} results with agentic retrieval")
>>> asyncio.run(run())
Retrieved 5 results with agentic retrieval
```

### async batch_retrieve

```python
batch_retrieve(queries: List[str], top_k: int = 5, **kwargs: Any) -> List[List[RetrievalResult]]
```

Batch retrieval, concurrently executing multiple queries.

**Parameters**:

* **queries**(List[str]): List of query strings.
* **top_k**(int): Number of results to return for each query. Default: 5.
* **kwargs**(Any): Variable arguments for passing additional configuration parameters.

**Returns**:

**List[List[RetrievalResult]]**, returns a list of retrieval results corresponding to each query.

### async close

```python
close() -> None
```

Close the retriever and release resources.

