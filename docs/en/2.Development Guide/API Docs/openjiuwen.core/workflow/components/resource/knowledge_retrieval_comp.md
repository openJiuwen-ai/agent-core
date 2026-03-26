# openjiuwen.core.workflow.components.resource.knowledge_retrieval_comp

## class ComponentKBConfig

Per-knowledge-base configuration model used inside the knowledge retrieval component.

**Parameters**:

* **kb_config**(KnowledgeBaseConfig): Knowledge base configuration.
* **vector_store_config**(VectorStoreConfig): Vector store configuration. `store_provider` determines the backend (Milvus, ChromaDB, PGVector).
* **embed_config**(EmbeddingConfig, optional): Embedding model configuration. Required when `index_type` is `"vector"` or `"hybrid"`. Default: None.
* **embed_additional_config**(Dict[str, Any]): Additional keyword arguments passed to the embedding model constructor. Default: {}.

## class KnowledgeRetrievalCompConfig

Configuration dataclass for the `KnowledgeRetrievalComponent`. Extends `ComponentConfig`. Supports multiple knowledge bases; retrieval is run over all configured bases and results are merged.

**Parameters**:

* **component_kb_configs**(List[ComponentKBConfig]): List of per-knowledge-base configs. Each entry defines one knowledge base (kb_config, vector_store_config, embed_config, etc.).
* **vector_store_connection_config**(Dict[str, Any]): Connection-related arguments for the vector store constructor (e.g. `{"chroma_path": "/tmp/chroma"}` or `{"milvus_uri": "http://localhost:19530", "milvus_token": ""}`).
* **retrieval_config**(RetrievalConfig): Retrieval configuration (top_k, score_threshold, graph/agentic options, etc.).
* **model_id**(str, optional): Model ID for retrieving an LLM from the Runner resource manager. Default: None.
* **model_client_config**(ModelClientConfig, optional): LLM client configuration for agentic retrieval. Default: None.
* **model_config**(ModelRequestConfig, optional): LLM request configuration for agentic retrieval. Default: None.

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
| `results` | List[str] | List of retrieved text strings (merged from all configured knowledge bases). |
| `context` | str | All retrieved texts joined with the default separator `"\n\n"`. |