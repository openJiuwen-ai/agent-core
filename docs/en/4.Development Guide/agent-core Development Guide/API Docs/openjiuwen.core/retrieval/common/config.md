# openjiuwen.core.retrieval.common.config

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
* **filters**(Dict[str, Any], optional): Metadata filter conditions. Default: None.

## class openjiuwen.core.retrieval.common.config.IndexConfig

Index configuration class, defining index-related configuration parameters.

**Parameters**:

* **index_name**(str): Index name.
* **index_type**(Literal["hybrid", "bm25", "vector"]): Index type. Default: "hybrid".

## class openjiuwen.core.retrieval.common.config.VectorStoreConfig

Vector store configuration class, defining vector store-related configuration parameters.

**Parameters**:

* **collection_name**(str): Collection name.
* **distance_metric**(Literal["cosine", "euclidean", "dot"]): Distance metric, cosine=cosine distance, euclidean=Euclidean distance, dot=dot product. Default: "cosine".

## class openjiuwen.core.retrieval.common.config.EmbeddingConfig

Embedding model configuration class, defining embedding model-related configuration parameters.

**Parameters**:

* **model_name**(str): Model name.
* **base_url**(str): API base URL.
* **api_key**(str, optional): API key. Default: None.

