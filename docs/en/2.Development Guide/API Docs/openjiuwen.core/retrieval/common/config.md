# openjiuwen.core.retrieval.common.config

> **Reference Examples**: For more usage examples, please refer to the example code in the [openJiuwen/agent-core](https://gitcode.com/openJiuwen/agent-core/) repository under the `examples/retrieval/` directory, including:
> - `chroma_query_expr.py` - ChromaDB query expression examples (using VectorStoreConfig)
> - `milvus_query_expr.py` - Milvus query expression examples (using VectorStoreConfig)
> - `configs.py` - Configuration class usage examples (using EmbeddingConfig, RerankerConfig)

## class openjiuwen.core.retrieval.common.config.KnowledgeBaseConfig

Knowledge base configuration class, defining basic configuration parameters for knowledge bases.

**Parameters**:

* **kb_id**(str): Knowledge base identifier.
* **index_type**(Literal["hybrid", "bm25", "vector"]): Index type, hybrid=hybrid index, bm25=BM25 index, vector=vector index. Default: "hybrid".
* **use_graph**(bool): Whether to use graph indexing. Default: False.
* **chunk_size**(int): Chunk size. Default: 512.
* **chunk_overlap**(int): Chunk overlap size. Default: 50.

## class openjiuwen.core.retrieval.common.config.RetrievalConfig

Retrieval configuration class, defining retrieval-related configuration parameters.

**Parameters**:

* **top_k**(int): Number of results to return. Default: 5.
* **score_threshold**(float, optional): Score threshold, results below this threshold will be filtered. Default: None.
* **use_graph**(bool, optional): Whether to use graph retrieval (uses default configuration when None). Default: None.
* **agentic**(bool): Whether to use agentic retrieval. Default: False.
* **graph_expansion**(bool): Whether to enable graph expansion. Default: False.
* **filters**(Dict[str, Any], optional): Metadata filter conditions (e.g., `{"category": "tech", "year": 2023}`). Default: None.

## class openjiuwen.core.retrieval.common.config.IndexConfig

Index configuration class, defining index-related configuration parameters.

**Parameters**:

* **index_name**(str): Index name.
* **index_type**(Literal["hybrid", "bm25", "vector"]): Index type. Default: "hybrid".

## class openjiuwen.core.retrieval.common.config.StoreType

Vector store provider type enumeration (extends `str, Enum`).

**Values**:

* **Milvus** = `"milvus"` — Milvus vector database.
* **Chroma** = `"chroma"` — ChromaDB vector database.
* **PGVector** = `"pgvector"` — PostgreSQL with pgvector extension.

## class openjiuwen.core.retrieval.common.config.VectorStoreConfig

Vector store configuration class, defining vector store-related configuration parameters.

**Parameters**:

* **store_provider**(StoreType): Vector store provider identification. Required. Accepted values: `"milvus"`, `"chroma"`, `"pgvector"` (or `StoreType` enum members).
* **database_name**(str): Database name. Default: "".
* **collection_name**(str): Collection name.
* **distance_metric**(Literal["cosine", "euclidean", "dot"]): Distance metric, cosine=cosine distance, euclidean=Euclidean distance, dot=dot product. Default: "cosine".

**Example**:

```python
>>> from openjiuwen.core.retrieval.common.config import VectorStoreConfig, StoreType
>>> config = VectorStoreConfig(
...     store_provider=StoreType.Milvus,
...     collection_name="my_collection",
...     distance_metric="cosine",
... )
>>> print(config.store_provider)  # StoreType.Milvus
```

## class openjiuwen.core.retrieval.common.config.EmbeddingConfig

Embedding model configuration class, defining embedding model-related configuration parameters.

**Parameters**:

* **model_name**(str): Model name.
* **base_url**(str): API base URL.
* **api_key**(str, optional): API key. Default: None.

## class openjiuwen.core.retrieval.common.config.RerankerConfig

Reranker configuration class, defining reranker-related configuration parameters.

**Parameters**:

* **api_key**(str): API key. Default: "".
* **api_base**(str): API base URL.
* **model_name**(str): Model name (accessible via alias "model"). Default: "".
* **timeout**(float): Request timeout in seconds. Default: 10.
* **temperature**(float): Temperature parameter. Default: 0.95.
* **top_p**(float): Top-p sampling parameter. Default: 0.1.
* **yes_no_ids**(tuple[int, int], optional): Token IDs for "yes" and "no" (e.g., `(123, 456)`). Default: None.
* **extra_body**(dict): Special keyword arguments (e.g., `{"custom_param": "value"}`). Default: {}.