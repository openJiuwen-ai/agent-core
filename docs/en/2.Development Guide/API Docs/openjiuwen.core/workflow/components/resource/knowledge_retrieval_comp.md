# openjiuwen.core.workflow.components.resource.knowledge_retrieval_comp

## class KnowledgeRetrievalCompConfig

Configuration dataclass for the `KnowledgeRetrievalComponent`. Extends `ComponentConfig`.

**Parameters**:

* **kb_configs**(List[KnowledgeBaseConfig]): List of knowledge base configurations. Each entry defines a knowledge base to retrieve from.
* **retrieval_config**(RetrievalConfig): Retrieval configuration (top_k, score_threshold, graph/agentic options, etc.).
* **vector_store_config**(VectorStoreConfig): Vector store configuration. The `store_provider` field determines which backend is used (Milvus, ChromaDB, PGVector).
* **vector_store_additional_config**(Dict[str, Any]): Additional keyword arguments passed to the vector store constructor (e.g., `{"milvus_uri": "http://localhost:19530"}` or `{"chroma_path": "/tmp/chroma"}`).
* **embed_config**(EmbeddingConfig, optional): Embedding model configuration. Required when `index_type` is `"vector"` or `"hybrid"`. Default: None.
* **model_id**(str, optional): Model ID for retrieving an LLM from the Runner resource manager. Default: None.
* **model_client_config**(ModelClientConfig, optional): LLM client configuration for agentic retrieval scenarios. Default: None.
* **model_config**(ModelRequestConfig, optional): LLM request configuration for agentic retrieval. Default: None.
* **result_separator**(str): Separator string used when joining retrieved texts into the `context` output. Default: `"\n\n"`.
* **include_metadata**(bool): Whether to include `results_with_metadata` in the output. Default: False.

## class KnowledgeRetrievalComponent

Composable workflow component for knowledge retrieval. Wraps `KnowledgeRetrievalExecutable` for use in workflow graphs.

```python
KnowledgeRetrievalComponent(component_config: Optional[KnowledgeRetrievalCompConfig] = None)
```

**Parameters**:

* **component_config**(KnowledgeRetrievalCompConfig, optional): Component configuration.

### Methods

#### add_component

```python
add_component(graph: Graph, node_id: str, wait_for_all: bool = False) -> None
```

Add this component as a node to the workflow graph.

#### to_executable

```python
to_executable() -> KnowledgeRetrievalExecutable
```

Convert the composable component into its executable counterpart.

## Input / Output

**Input** (`KnowledgeRetrievalInput`):

| Field | Type | Description |
|-------|------|-------------|
| `query` | str | The query string to retrieve documents for. |

> **Query rewriting**: When agentic retrieval is not enabled, the component does not rewrite the query. For multi-turn dialogue, pre-rewrite the user message with [QueryRewriter](../../../retrieval/query_rewriter/query_rewriter.md) and pass the returned `standalone_query` as `query`. See the openJiuwen [Knowledge Retrieval](../../../../../../Advanced%20Usage/Knowledge%20Retrieval.md) guide.

**Output**:

| Field | Type | Description |
|-------|------|-------------|
| `results` | List[str] | List of retrieved text strings. |
| `context` | str | All retrieved texts joined by `result_separator`. |
| `results_with_metadata` | List[dict] (optional) | Only present when `include_metadata=True`. Each item is a serialized `MultiKBRetrievalResult`. |