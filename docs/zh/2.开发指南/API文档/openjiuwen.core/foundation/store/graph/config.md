# openjiuwen.core.foundation.store.graph.config / database_config / result_ranking / constants

图存储配置、索引与存储限制、检索排序配置及常量定义。

## 常量（constants）

| 常量 | 类型 | 说明 |
|------|------|------|
| **ENTITY_COLLECTION** | str | 实体集合名，用于 `collection` 参数。 |
| **RELATION_COLLECTION** | str | 关系集合名。 |
| **EPISODE_COLLECTION** | str | 情节集合名。 |

---

## class GraphConfig

```python
class openjiuwen.core.foundation.store.graph.config.GraphConfig(BaseModel)
```

图存储整体配置，包含连接、后端、嵌入维度、索引与存储配置等。

```python
GraphConfig(
    uri: str,
    name: str = "",
    token: str = "",
    backend: str = "milvus",
    timeout: int | float = 15.0,
    extras: dict = {},
    max_concurrent: int = 10,
    embed_dim: int = 512,
    embed_batch_size: int = 10,
    embedding_model: Embedding | None = None,
    db_storage_config: GraphStoreStorageConfig = ...,
    db_embed_config: GraphStoreIndexConfig = ...,
    request_max_retries: int = 5,
)
```

**参数**：

- **uri**(str)：图数据库连接地址（如 `"http://localhost:19530"`）或本地路径。
- **name**(str, 可选)：数据库/库名。默认值：""。
- **token**(str, 可选)：认证 token。默认值：""。
- **backend**(str, 可选)：后端类型，当前支持 `"milvus"`。默认值："milvus"。
- **timeout**(int | float, 可选)：连接与操作超时（秒），需 > 0。默认值：15.0。
- **extras**(dict, 可选)：传给底层客户端的额外参数（如 `alias`）。键必须为 str。默认值：{}。
- **max_concurrent**(int, 可选)：最大并发数，≥ 0。默认值：10。
- **embed_dim**(int, 可选)：嵌入维度，≥ 32。默认值：512。
- **embed_batch_size**(int, 可选)：嵌入批大小，≥ 1。默认值：10。
- **embedding_model**(Embedding | None, 可选)：嵌入模型，也可后续通过 `attach_embedder` 绑定。默认值：None。
- **db_storage_config**(GraphStoreStorageConfig, 可选)：存储字段长度等限制。默认值：GraphStoreStorageConfig()。
- **db_embed_config**(GraphStoreIndexConfig)：向量索引与距离度量配置，必填。
- **request_max_retries**(int, 可选)：请求最大重试次数。默认值：5。

---

## class GraphStoreIndexConfig

```python
class openjiuwen.core.foundation.store.graph.database_config.GraphStoreIndexConfig(BaseModel)
```

图库向量索引配置：索引类型、距离度量、BM25 等。

```python
GraphStoreIndexConfig(
    index_type: VectorField,
    distance_metric: Literal["cosine", "euclidean", "dot"],
    extra_configs: Dict[str, Any] = {},
    bm25_config: BM25Config | BaseModel = ...,
    bm25_analyzer_settings: Dict[str, Any] | None = None,
)
```

**参数**：

- **index_type**(VectorField)：ANN 索引类型，如 [MilvusAUTO](../vector_fields/milvus_fields.md)。更多关于 VectorField 的配置选项，请参考 [VectorField 文档](../vector_fields/base.md)。
- **distance_metric**(Literal["cosine", "euclidean", "dot"])：距离度量。
- **extra_configs**(Dict[str, Any], 可选)：额外索引配置。默认值：{}。
- **bm25_config**(BM25Config | BaseModel, 可选)：BM25 参数。默认值：BM25Config()。
- **bm25_analyzer_settings**(Dict[str, Any] | None, 可选)：BM25 分词器配置。默认值：None。

---

## class GraphStoreStorageConfig

```python
class openjiuwen.core.foundation.store.graph.database_config.GraphStoreStorageConfig(BaseModel)
```

图库存储字段长度与数量限制（如 uuid、name、content、entities 数组长度等）。

**参数**（均为可选，仅列常用）：

- **uuid**(int, 可选)：uuid 最大长度。默认值：32。
- **name**(int, 可选)：name 最大长度。默认值：500。
- **content**(int, 可选)：content 最大长度。默认值：65535。
- **language**(int, 可选)：language 最大长度。默认值：10。
- **user_id**(int, 可选)：user_id 最大长度。默认值：32。
- **entities**(int, 可选)：每个 Episode 关联实体的最大数量。默认值：4096。
- **relations**(int, 可选)：每个 Entity 关联关系的最大数量。默认值：4096。
- **episodes**(int, 可选)：每个 Entity 关联情节的最大数量。默认值：4096。
- **obj_type**(int, 可选)：obj_type 最大长度。默认值：20。

---

## class BaseRankConfig

```python
class openjiuwen.core.foundation.store.graph.result_ranking.BaseRankConfig(BaseModel, ABC)
```

混合检索排序配置基类。通道包括：name_dense（名称向量）、content_dense（内容向量）、content_sparse（内容 BM25）。

### property args

```text
args -> tuple[list, dict]
```

构造排序器时的 (位置参数列表, 关键字参数字典)。

### property is_active

```text
is_active -> list[int]
```

各通道是否启用（name_dense, content_dense, content_sparse），非 0 表示启用。默认 `[1, 1, 1]`。

### get_ranker_cls

```python
def get_ranker_cls(database: str) -> Any
```

根据后端名获取该配置对应的排序器类。

**参数**：

- **database**(str)：后端名（如 `"milvus"`）。

**返回**：

- **Callable | None**：排序器类或 None。

---

## class WeightedRankConfig

```python
class openjiuwen.core.foundation.store.graph.result_ranking.WeightedRankConfig(BaseRankConfig)
```

按权重合并 name_dense、content_dense、content_sparse 三路分数；权重会归一化，0 表示不使用该通道。

```python
WeightedRankConfig(
    name: str = "weighted",
    name_dense: float = 0.15,
    content_dense: float = 0.6,
    content_sparse: float = 0.25,
)
```

**参数**：

- **name**(str, 可选)：配置名。默认值："weighted"。
- **name_dense**(float, 可选)：名称向量权重，[0, 1]。默认值：0.15。
- **content_dense**(float, 可选)：内容向量权重，[0, 1]。默认值：0.6。
- **content_sparse**(float, 可选)：内容 BM25 权重，[0, 1]。默认值：0.25。

---

## class RRFRankConfig

```python
class openjiuwen.core.foundation.store.graph.result_ranking.RRFRankConfig(BaseRankConfig)
```

使用 RRF（Reciprocal Rank Fusion）合并多路排序结果；通过 name_dense、content_dense、content_sparse 布尔值控制参与融合的通道。

```python
RRFRankConfig(
    name: str = "rrf",
    higher_is_better: bool = True,
    k: int = 40,
    name_dense: bool = True,
    content_dense: bool = True,
    content_sparse: bool = True,
)
```

**参数**：

- **name**(str, 可选)：配置名。默认值："rrf"。
- **higher_is_better**(bool, 可选)：分数越高越优。默认值：True。
- **k**(int, 可选)：RRF 常数 k，≥ 0。默认值：40。
- **name_dense**(bool, 可选)：是否包含名称向量通道。默认值：True。
- **content_dense**(bool, 可选)：是否包含内容向量通道。默认值：True。
- **content_sparse**(bool, 可选)：是否包含 BM25 通道。默认值：True。
