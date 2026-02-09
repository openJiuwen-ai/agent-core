# openjiuwen.core.retrieval.indexing.vector_fields.chroma_fields

## class openjiuwen.core.retrieval.indexing.vector_fields.chroma_fields.ChromaVectorField

HNSW index configuration for ChromaDB vector database.

ChromaDB uses Hierarchical Navigable Small World (HNSW) algorithm for approximate nearest neighbor search. HNSW builds a multi-layer graph structure that provides excellent search performance and accuracy, especially for high-dimensional vectors.


```python
ChromaVectorField(vector_field: str = "embedding", max_neighbors: int = 16, ef_construction: int = 100, ef_search: float = 100, extra_search: dict[str, Any] = {})
```

Initialize ChromaDB HNSW index configuration.

**Parameters**:

* **vector_field**(str, optional): Vector field name. Default: "embedding".
* **max_neighbors**(int, optional): Maximum number of edges per node in the HNSW graph. Higher values improve accuracy but increase memory usage and construction time. Default: 16, Range: [2, 2048].
* **ef_construction**(int, optional): Number of candidate neighbors to consider during index construction. Higher values improve graph quality but slow construction. Default: 100, Range: [1, ∞).
* **ef_search**(float, optional): Number of candidates to explore during search. This directly controls the search breadth - higher values improve recall at the cost of increased latency. Default: 100, Range: [1, ∞).
* **extra_search**(dict[str, Any], optional): Additional search parameters for fine-tuning performance. Default: {}.

**extra_search parameters**:

* **resize_factor**(int/float): Factor for dynamic graph resizing.
* **num_threads**(int): Number of threads for parallel search.
* **batch_size**(int): Batch size for batch search operations.
* **sync_threshold**(int): Threshold for synchronization operations.
