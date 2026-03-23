# openjiuwen.core.foundation.store.graph.config / database_config / result_ranking / constants

Graph store configuration, index and storage limits, search ranking config, and constants.

## Constants (constants)

| Constant | Type | Description |
|----------|------|-------------|
| **ENTITY_COLLECTION** | str | Entity collection name for `collection` parameter. |
| **RELATION_COLLECTION** | str | Relation collection name. |
| **EPISODE_COLLECTION** | str | Episode collection name. |

---

## class GraphConfig

```python
class openjiuwen.core.foundation.store.graph.config.GraphConfig(BaseModel)
```

Overall graph store configuration: connection, backend, embed dimension, index and storage config.

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

**Parameters**:

- **uri**(str): Graph database URI (e.g. `"http://localhost:19530"`) or local path.
- **name**(str, optional): Database name. Default: "".
- **token**(str, optional): Auth token. Default: "".
- **backend**(str, optional): Backend type; currently `"milvus"`. Default: "milvus".
- **timeout**(int | float, optional): Connection/operation timeout (seconds), must be > 0. Default: 15.0.
- **extras**(dict, optional): Extra client options (e.g. `alias`); keys must be str. Default: {}.
- **max_concurrent**(int, optional): Max concurrency, ≥ 0. Default: 10.
- **embed_dim**(int, optional): Embedding dimension, ≥ 32. Default: 512.
- **embed_batch_size**(int, optional): Embedding batch size, ≥ 1. Default: 10.
- **embedding_model**(Embedding | None, optional): Embedding model; can also be set via `attach_embedder`. Default: None.
- **db_storage_config**(GraphStoreStorageConfig, optional): Storage field limits. Default: GraphStoreStorageConfig().
- **db_embed_config**(GraphStoreIndexConfig): Vector index and distance metric config; required.
- **request_max_retries**(int, optional): Max request retries. Default: 5.

---

## class GraphStoreIndexConfig

```python
class openjiuwen.core.foundation.store.graph.database_config.GraphStoreIndexConfig(BaseModel)
```

Graph store vector index config: index type, distance metric, BM25, etc.

```python
GraphStoreIndexConfig(
    index_type: VectorField,
    distance_metric: Literal["cosine", "euclidean", "dot"],
    extra_configs: Dict[str, Any] = {},
    bm25_config: BM25Config | BaseModel = ...,
    bm25_analyzer_settings: Dict[str, Any] | None = None,
)
```

**Parameters**:

- **index_type**(VectorField): ANN index type, e.g. [MilvusAUTO](../vector_fields/milvus_fields.md). For more VectorField options, see [VectorField documentation](../vector_fields/base.md).
- **distance_metric**(Literal["cosine", "euclidean", "dot"]): Distance metric.
- **extra_configs**(Dict[str, Any], optional): Extra index config. Default: {}.
- **bm25_config**(BM25Config | BaseModel, optional): BM25 options. Default: BM25Config().
- **bm25_analyzer_settings**(Dict[str, Any] | None, optional): BM25 analyzer settings. Default: None.

---

## class GraphStoreStorageConfig

```python
class openjiuwen.core.foundation.store.graph.database_config.GraphStoreStorageConfig(BaseModel)
```

Storage field length and count limits (uuid, name, content, entities array size, etc.).

**Parameters** (all optional; common ones):

- **uuid**(int, optional): Max uuid length. Default: 32.
- **name**(int, optional): Max name length. Default: 500.
- **content**(int, optional): Max content length. Default: 65535.
- **language**(int, optional): Max language length. Default: 10.
- **user_id**(int, optional): Max user_id length. Default: 32.
- **entities**(int, optional): Max entities per episode. Default: 4096.
- **relations**(int, optional): Max relations per entity. Default: 4096.
- **episodes**(int, optional): Max episodes per entity. Default: 4096.
- **obj_type**(int, optional): Max obj_type length. Default: 20.

---

## class BaseRankConfig

```python
class openjiuwen.core.foundation.store.graph.result_ranking.BaseRankConfig(BaseModel, ABC)
```

Base class for hybrid search ranking config. Channels: name_dense, content_dense, content_sparse.

### property args

```text
args -> tuple[list, dict]
```

(Positional args, keyword args) for constructing the ranker.

### property is_active

```text
is_active -> list[int]
```

Per-channel active flags (name_dense, content_dense, content_sparse); non-zero means active. Default `[1, 1, 1]`.

### get_ranker_cls

```python
def get_ranker_cls(database: str) -> Any
```

Get the ranker class for this config and backend name.

**Parameters**:

- **database**(str): Backend name (e.g. `"milvus"`).

**Returns**:

- **Callable | None**: Ranker class or None.

---

## class WeightedRankConfig

```python
class openjiuwen.core.foundation.store.graph.result_ranking.WeightedRankConfig(BaseRankConfig)
```

Weighted combination of name_dense, content_dense, content_sparse; weights are normalized, 0 disables a channel.

```python
WeightedRankConfig(
    name: str = "weighted",
    name_dense: float = 0.15,
    content_dense: float = 0.6,
    content_sparse: float = 0.25,
)
```

**Parameters**:

- **name**(str, optional): Config name. Default: "weighted".
- **name_dense**(float, optional): Name vector weight, [0, 1]. Default: 0.15.
- **content_dense**(float, optional): Content vector weight, [0, 1]. Default: 0.6.
- **content_sparse**(float, optional): Content BM25 weight, [0, 1]. Default: 0.25.

---

## class RRFRankConfig

```python
class openjiuwen.core.foundation.store.graph.result_ranking.RRFRankConfig(BaseRankConfig)
```

RRF (Reciprocal Rank Fusion) to merge multiple ranked lists; name_dense, content_dense, content_sparse booleans control which channels participate.

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

**Parameters**:

- **name**(str, optional): Config name. Default: "rrf".
- **higher_is_better**(bool, optional): Higher score is better. Default: True.
- **k**(int, optional): RRF constant k, ≥ 0. Default: 40.
- **name_dense**(bool, optional): Include name vector channel. Default: True.
- **content_dense**(bool, optional): Include content vector channel. Default: True.
- **content_sparse**(bool, optional): Include BM25 channel. Default: True.
