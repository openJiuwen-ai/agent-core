# openjiuwen.core.foundation.store.vector_fields.milvus_fields

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusFLAT

Milvus 的 FLAT 索引配置。

FLAT 索引执行精确最近邻搜索，不进行近似。提供最高准确性，但与近似方法相比，内存使用更高，搜索速度更慢。适用于中小型数据集。


```python
MilvusFLAT(vector_field: str = "embedding")
```

初始化 Milvus FLAT 索引配置。

**参数**：

* **vector_field**(str, 可选)：向量字段名称。默认值："embedding"。

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusAUTO

Milvus 的 AUTOINDEX 配置。

AUTOINDEX 是 Milvus 中的默认索引类型，在性能和易用性之间提供良好的平衡。

在部署 Milvus 数据库时可在 milvus.yaml 中配置。默认值为 {"M": 18, "efConstruction": 240, "index_type": "HNSW", "metric_type": "COSINE"}。


```python
MilvusAUTO(vector_field: str = "embedding")
```

初始化 Milvus AUTOINDEX 配置。

**参数**：

* **vector_field**(str, 可选)：向量字段名称。默认值："embedding"。

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusSCANN

Milvus 的 SCANN（Scalable Nearest Neighbors）索引配置。

SCANN 是基于 IVF 的索引，使用乘积量化进行压缩。它在搜索速度、准确性和内存使用之间提供了良好的平衡，适用于大规模部署。

继承自 IVF 聚类参数（nlist、nprobe）。


```python
MilvusSCANN(vector_field: str = "embedding", nlist: int = 128, nprobe: int = 8, with_raw_data: bool = True, reorder_k: int = None)
```

初始化 Milvus SCANN 索引配置。

**参数**：

* **vector_field**(str, 可选)：向量字段名称。默认值："embedding"。
* **nlist**(int, 可选)：索引构建期间创建的聚类数量。值越大，准确性越高，但构建时间越长。默认值：128，范围：[1, 65536]。
* **nprobe**(int, 可选)：查询时搜索的聚类数量。必须 <= nlist。值越大，召回率越高，但延迟也会增加。默认值：8，范围：[1, 65536]。
* **with_raw_data**(bool, 可选)：是否存储原始向量。设置为 True 可提高准确性，但会增加存储。默认值：True。
* **reorder_k**(int, 可选)：搜索期间使用更高精度向量重新排序的结果数量。仅在 with_raw_data 为 True 时有效。值越大，准确性越高，但延迟也会增加。默认值：None（不重新排序）。

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusIVF

Milvus 的倒排文件（IVF）索引配置。

IVF 使用 k-means 算法将向量空间划分为聚类，并在查询时仅搜索最相关的聚类。这在搜索速度和准确性之间提供了良好的平衡。

支持不同的量化变体以进一步优化内存使用：
* FLAT：无量化，最高准确性但内存使用最高
* SQ8：8 位标量量化，内存减少约 75%
* PQ：乘积量化，可通过 m 和 nbits 配置压缩
* RABITQ：残差感知二进制量化，最佳压缩


```python
MilvusIVF(vector_field: str = "embedding", variant: Literal["FLAT", "SQ8", "PQ", "RABITQ"] = "FLAT", nlist: int = 128, nprobe: int = 8, extra_construct: dict[str, Any] = {}, extra_search: dict[str, Any] = {})
```

初始化 Milvus IVF 索引配置。

**参数**：

* **vector_field**(str, 可选)：向量字段名称。默认值："embedding"。
* **variant**(Literal["FLAT", "SQ8", "PQ", "RABITQ"], 可选)：要使用的量化变体。确定哪些额外参数对构建和搜索阶段有效。默认值："FLAT"。
* **nlist**(int, 可选)：索引构建期间创建的聚类数量。值越大，准确性越高，但构建时间越长。默认值：128，范围：[1, 65536]。
* **nprobe**(int, 可选)：查询时搜索的聚类数量。必须 <= nlist。值越大，召回率越高，但延迟也会增加。默认值：8，范围：[1, 65536]。
* **extra_construct**(dict[str, Any], 可选)：索引构建的额外参数，根据所选变体进行验证。默认值：{}。
* **extra_search**(dict[str, Any], 可选)：搜索的额外参数，根据所选变体进行验证。默认值：{}。

**extra_construct 参数（PQ 变体）**：
* **m**(int)：子向量数量，范围：[1, 65536]
* **nbits**(int)：每个子向量的位数，范围：[1, 24]，默认值：8

**extra_construct 参数（RABITQ 变体）**：
* **refine**(bool)：是否启用细化
* **refine_type**(str)：细化类型，必须是 ["SQ6", "SQ8", "FP16", "BF16", "FP32"] 之一（如果 refine 为 True）

**extra_search 参数（RABITQ 变体）**：
* **refine_k**(float)：细化因子，必须 >= 1
* **rbq_query_bits**(int)：查询位数，范围：[0, 8]

## class openjiuwen.core.foundation.store.vector_fields.milvus_fields.MilvusHNSW

Milvus 的分层可导航小世界（HNSW）索引配置。

HNSW 构建多层图结构以实现高效的近似最近邻搜索。它提供出色的搜索性能和准确性，特别适用于高维向量。通常推荐用于大多数用例。

支持可选的量化变体以减少内存使用：
* SQ：标量量化，内存减少约 75%，准确性损失最小
* PQ：乘积量化，可通过 m 和 nbits 配置压缩
* PRQ：乘积残差量化，最佳压缩比


```python
MilvusHNSW(vector_field: str = "embedding", M: int = 30, efConstruction: int = 360, efSearchFactor: float = None, variant: Optional[Literal["SQ", "PQ", "PRQ"]] = None, extra_construct: dict[str, Any] = {}, extra_search: dict[str, Any] = {})
```

初始化 Milvus HNSW 索引配置。

**参数**：

* **vector_field**(str, 可选)：向量字段名称。默认值："embedding"。
* **M**(int, 可选)：图中每个节点的最大边数。值越大，准确性越高，但会增加内存使用和构建时间。默认值：30，范围：[2, 2048]。
* **efConstruction**(int, 可选)：索引构建期间考虑的候选邻居数量。值越大，图质量越好，但构建速度越慢。默认值：360，范围：[1, ∞)。
* **efSearchFactor**(float, 可选)：搜索广度的乘数。如果设置，ef = top_k * efSearchFactor。值越大，召回率越高，但延迟也会增加。默认值：None。
* **variant**(Optional[Literal["SQ", "PQ", "PRQ"]], 可选)：可选的量化变体以减少内存使用。选项："SQ"、"PQ"、"PRQ" 或 None（无量化）。默认值：None。
* **extra_construct**(dict[str, Any], 可选)：索引构建的额外参数，根据所选变体进行验证。默认值：{}。
* **extra_search**(dict[str, Any], 可选)：搜索的额外参数，根据所选变体进行验证。默认值：{}。

**extra_construct 参数（SQ 变体）**：
* **sq_type**(str)：标量量化类型，必须是 ["SQ4U", "SQ6", "SQ8", "FP16", "BF16"] 之一，默认值："SQ8"
* **refine**(bool)：是否启用细化
* **refine_type**(str)：细化类型，必须是 ["SQ6", "SQ8", "FP16", "BF16", "FP32"] 之一（如果 refine 为 True）

**extra_construct 参数（PQ/PRQ 变体）**：
* **m**(int)：子向量数量，范围：[1, 65536]
* **nbits**(int)：每个子向量的位数，范围：[1, 24]，默认值：8
* **nrq**(int, 仅 PRQ)：残差量化数量，范围：[1, 16]，默认值：2

**extra_search 参数（PQ/PRQ 变体）**：
* **refine_k**(float)：细化因子，必须 >= 1
