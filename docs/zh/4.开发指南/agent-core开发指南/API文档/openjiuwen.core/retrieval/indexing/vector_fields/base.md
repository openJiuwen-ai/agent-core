# openjiuwen.core.retrieval.indexing.vector_fields.base

## class openjiuwen.core.retrieval.indexing.vector_fields.base.VectorField

向量字段配置基类，用于配置向量数据库中的近似最近邻（ANN）搜索索引。

提供跨不同数据库后端的向量字段索引配置的统一接口。支持基于阶段的字段过滤，以分离构建和搜索参数。


```python
VectorField(vector_field: str = "embedding", database_type: Literal["milvus", "chroma"], index_type: Literal["auto", "hnsw", "flat", "ivf", "scann"])
```

初始化向量字段配置。

**参数**：

* **vector_field**(str, 可选)：数据库中的向量字段名称。默认值："embedding"。
* **database_type**(Literal["milvus", "chroma"])：向量数据库类型（milvus 或 chroma）。
* **index_type**(Literal["auto", "hnsw", "flat", "ivf", "scann"])：ANN 索引类型（auto、hnsw、flat、ivf 或 scann）。

### staticmethod should_keep

```python
should_keep(finfo: FieldInfo, stage: Literal["search", "construct"]) -> bool
```

判断字段是否应在给定阶段保留。

字段可以在其 json_schema_extra 中标记 "stage"，以指示它们仅与 "construct" 或 "search" 操作相关。

**参数**：

* **finfo**(FieldInfo)：包含字段元数据的 Pydantic FieldInfo。
* **stage**(Literal["search", "construct"])：要检查的阶段："search"（仅搜索）或 "construct"（仅构建）。

**返回**：

**bool**，如果字段应在该阶段保留则返回 True，否则返回 False。

### to_dict

```python
to_dict(stage: Literal["search", "construct"]) -> dict[str, Any]
```

将向量字段配置转换为特定阶段的字典。

根据指定阶段过滤字段，并合并该阶段的额外参数。移除内部字段（database_type、index_type、vector_field）和不匹配当前阶段的阶段特定额外字段。

**参数**：

* **stage**(Literal["search", "construct"])：要生成配置的阶段："search"（搜索操作的字段和 extra_search 参数）或 "construct"（索引构建的字段和 extra_construct 参数）。

**返回**：

**dict[str, Any]**，返回仅包含该阶段相关字段的字典，并合并了额外参数。
