# openjiuwen.core.retrieval.retriever.agentic_retriever

## class openjiuwen.core.retrieval.retriever.agentic_retriever.AgenticRetriever

Agentic retriever that adds LLM query rewriting and multi-round fusion capabilities on top of any retriever, improving retrieval effectiveness through iterative retrieval and query optimization.

When the underlying retriever is a `GraphRetriever`, graph-specific features such as graph expansion and triple linking are enabled automatically. For any other `Retriever` subclass, the agent performs iterative query rewriting and result fusion directly.


```python
AgenticRetriever(retriever: Retriever, llm_client: BaseModelClient, max_iter: int = 2)
```

Initialize agentic retriever.

**Parameters**:

* **retriever**(Retriever): The underlying retriever instance. Accepts any `Retriever` subclass. When a `GraphRetriever` is supplied, graph-specific features are enabled automatically.
* **llm_client**(BaseModelClient): LLM client instance used for triple extraction and query rewriting.
* **max_iter**(int): Maximum number of agent iterations. Default: 2.

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
>>> from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever
>>> from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
>>> 
>>> async def run():
...     # Create LLM client
...     llm_client = OpenAIModelClient(...)
...
...     # Example 1: With a GraphRetriever (enables graph-specific features)
...     graph_retriever = GraphRetriever(...)
...     agentic = AgenticRetriever(
...         retriever=graph_retriever,
...         llm_client=llm_client,
...         max_iter=2,
...     )
...     results = await agentic.retrieve("test query", top_k=5)
...     print(f"Retrieved {len(results)} results with graph-agentic retrieval")
...
...     # Example 2: With any other Retriever (e.g., VectorRetriever)
...     vector_retriever = VectorRetriever(...)
...     agentic = AgenticRetriever(
...         retriever=vector_retriever,
...         llm_client=llm_client,
...         max_iter=2,
...     )
...     results = await agentic.retrieve("test query", top_k=5)
...     print(f"Retrieved {len(results)} results with generic agentic retrieval")
>>> asyncio.run(run())
Retrieved 5 results with graph-agentic retrieval
Retrieved 5 results with generic agentic retrieval
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

