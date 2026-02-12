# openjiuwen.core.foundation.store.vector_fields.milvus_fields

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusFLAT

FLAT index configuration for Milvus.

FLAT index performs exact nearest neighbor search without approximation. Provides the highest accuracy but with higher memory usage and slower search compared to approximate methods. Suitable for small to medium-sized datasets.


```python
MilvusFLAT(vector_field: str = "embedding")
```

Initialize Milvus FLAT index configuration.

**Parameters**:

* **vector_field**(str, optional): Vector field name. Default: "embedding".

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusAUTO

AUTOINDEX configuration for Milvus.

AUTOINDEX is the default index type in Milvus, providing good balance between performance and ease of use.

Configurable in milvus.yaml when deploying Milvus database. Defaults to {"M": 18, "efConstruction": 240, "index_type": "HNSW", "metric_type": "COSINE"}.


```python
MilvusAUTO(vector_field: str = "embedding")
```

Initialize Milvus AUTOINDEX configuration.

**Parameters**:

* **vector_field**(str, optional): Vector field name. Default: "embedding".

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusSCANN

SCANN (Scalable Nearest Neighbors) index configuration for Milvus.

SCANN is an IVF-based index that uses product quantization for compression. It provides a good balance between search speed, accuracy, and memory usage, making it suitable for large-scale deployments.

Inherits IVF clustering parameters (nlist, nprobe) from MilvusIVFBase.


```python
MilvusSCANN(vector_field: str = "embedding", nlist: int = 128, nprobe: int = 8, with_raw_data: bool = True, reorder_k: int = None)
```

Initialize Milvus SCANN index configuration.

**Parameters**:

* **vector_field**(str, optional): Vector field name. Default: "embedding".
* **nlist**(int, optional): Number of clusters to create during index construction. Higher values improve accuracy but increase construction time. Default: 128, Range: [1, 65536].
* **nprobe**(int, optional): Number of clusters to search during query time. Must be <= nlist. Higher values improve recall at the cost of latency. Default: 8, Range: [1, 65536].
* **with_raw_data**(bool, optional): Whether to store original vectors. Setting to True improves accuracy at the cost of increased storage. Default: True.
* **reorder_k**(int, optional): Number of results to reorder using higher precision vectors during search. Only effective when with_raw_data is True. Higher values improve accuracy but increase latency. Default: None (no reordering).

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusIVF

Inverted File (IVF) index configuration for Milvus.

IVF divides the vector space into clusters using k-means algorithm and searches only the most relevant clusters during query time. This provides a good balance between search speed and accuracy.

Supports different quantization variants to further optimize memory usage:
* FLAT: No quantization, highest accuracy but highest memory usage
* SQ8: Scalar quantization with 8-bit precision, reduces memory by ~75%
* PQ: Product quantization, configurable compression via m and nbits
* RABITQ: Residual-aware binary quantization, best compression


```python
MilvusIVF(vector_field: str = "embedding", variant: Literal["FLAT", "SQ8", "PQ", "RABITQ"] = "FLAT", nlist: int = 128, nprobe: int = 8, extra_construct: dict[str, Any] = {}, extra_search: dict[str, Any] = {})
```

Initialize Milvus IVF index configuration.

**Parameters**:

* **vector_field**(str, optional): Vector field name. Default: "embedding".
* **variant**(Literal["FLAT", "SQ8", "PQ", "RABITQ"], optional): The quantization variant to use. Determines which extra arguments are valid for construction and search stages. Default: "FLAT".
* **nlist**(int, optional): Number of clusters to create during index construction. Higher values improve accuracy but increase construction time. Default: 128, Range: [1, 65536].
* **nprobe**(int, optional): Number of clusters to search during query time. Must be <= nlist. Higher values improve recall at the cost of latency. Default: 8, Range: [1, 65536].
* **extra_construct**(dict[str, Any], optional): Additional parameters for index construction, validated based on the selected variant. Default: {}.
* **extra_search**(dict[str, Any], optional): Additional parameters for search, validated based on the selected variant. Default: {}.

**extra_construct parameters (PQ variant)**:
* **m**(int): Number of sub-vectors, Range: [1, 65536]
* **nbits**(int): Number of bits per sub-vector, Range: [1, 24], Default: 8

**extra_construct parameters (RABITQ variant)**:
* **refine**(bool): Whether to enable refinement
* **refine_type**(str): Type of refinement, must be one of ["SQ6", "SQ8", "FP16", "BF16", "FP32"] if refine is True

**extra_search parameters (RABITQ variant)**:
* **refine_k**(float): Refinement factor, must be >= 1
* **rbq_query_bits**(int): Query bits, Range: [0, 8]

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusHNSW

Hierarchical Navigable Small World (HNSW) index configuration for Milvus.

HNSW builds a multi-layer graph structure for efficient approximate nearest neighbor search. It provides excellent search performance and accuracy, especially for high-dimensional vectors. Generally recommended for most use cases.

Supports optional quantization variants to reduce memory usage:
* SQ: Scalar quantization, reduces memory by ~75% with minimal accuracy loss
* PQ: Product quantization, configurable compression via m and nbits
* PRQ: Product residual quantization, best compression ratio


```python
MilvusHNSW(vector_field: str = "embedding", M: int = 30, efConstruction: int = 360, efSearchFactor: float = None, variant: Optional[Literal["SQ", "PQ", "PRQ"]] = None, extra_construct: dict[str, Any] = {}, extra_search: dict[str, Any] = {})
```

Initialize Milvus HNSW index configuration.

**Parameters**:

* **vector_field**(str, optional): Vector field name. Default: "embedding".
* **M**(int, optional): Maximum number of edges per node in the graph. Higher values improve accuracy but increase memory usage and construction time. Default: 30, Range: [2, 2048].
* **efConstruction**(int, optional): Number of candidate neighbors to consider during index construction. Higher values improve graph quality but slow construction. Default: 360, Range: [1, ∞).
* **efSearchFactor**(float, optional): Multiplier for search breadth. If set, ef = top_k * efSearchFactor. Higher values improve recall at the cost of latency. Default: None.
* **variant**(Optional[Literal["SQ", "PQ", "PRQ"]], optional): Optional quantization variant to reduce memory usage. Options: "SQ", "PQ", "PRQ", or None (no quantization). Default: None.
* **extra_construct**(dict[str, Any], optional): Additional parameters for index construction, validated based on the selected variant. Default: {}.
* **extra_search**(dict[str, Any], optional): Additional parameters for search, validated based on the selected variant. Default: {}.

**extra_construct parameters (SQ variant)**:
* **sq_type**(str): Scalar quantization type, must be one of ["SQ4U", "SQ6", "SQ8", "FP16", "BF16"], Default: "SQ8"
* **refine**(bool): Whether to enable refinement
* **refine_type**(str): Type of refinement, must be one of ["SQ6", "SQ8", "FP16", "BF16", "FP32"] if refine is True

**extra_construct parameters (PQ/PRQ variant)**:
* **m**(int): Number of sub-vectors, Range: [1, 65536]
* **nbits**(int): Number of bits per sub-vector, Range: [1, 24], Default: 8
* **nrq**(int, PRQ only): Number of residual quantizations, Range: [1, 16], Default: 2

**extra_search parameters (PQ/PRQ variant)**:
* **refine_k**(float): Refinement factor, must be >= 1
