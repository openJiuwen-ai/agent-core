# openjiuwen.core.retrieval.indexing.vector_fields.chroma_fields

## class openjiuwen.core.retrieval.indexing.vector_fields.chroma_fields.ChromaVectorField

ChromaDB 向量数据库的 HNSW 索引配置。

ChromaDB 使用分层可导航小世界（HNSW）算法进行近似最近邻搜索。HNSW 构建多层图结构，提供出色的搜索性能和准确性，特别适用于高维向量。


```python
ChromaVectorField(vector_field: str = "embedding", max_neighbors: int = 16, ef_construction: int = 100, ef_search: float = 100, extra_search: dict[str, Any] = {})
```

初始化 ChromaDB HNSW 索引配置。

**参数**：

* **vector_field**(str, 可选)：向量字段名称。默认值："embedding"。
* **max_neighbors**(int, 可选)：HNSW 图中每个节点的最大边数。值越大，准确性越高，但会增加内存使用和构建时间。默认值：16，范围：[2, 2048]。
* **ef_construction**(int, 可选)：索引构建期间考虑的候选邻居数量。值越大，图质量越好，但构建速度越慢。默认值：100，范围：[1, ∞)。
* **ef_search**(float, 可选)：搜索期间探索的候选数量。这直接控制搜索广度，值越大，召回率越高，但延迟也会增加。默认值：100，范围：[1, ∞)。
* **extra_search**(dict[str, Any], 可选)：用于微调性能的额外搜索参数。默认值：{}。

**extra_search 参数说明**：

* **resize_factor**(int/float)：动态图调整大小的因子。
* **num_threads**(int)：并行搜索的线程数。
* **batch_size**(int)：批量搜索操作的批次大小。
* **sync_threshold**(int)：同步操作的阈值。
