# openjiuwen.core.foundation.store.graph.base_graph_store / base / milvus

图结构向量存储协议及 Milvus 实现：定义 GraphStore 接口、工厂类 GraphStoreFactory 与 MilvusGraphStore 实现。

## class GraphStore

```python
class openjiuwen.core.foundation.store.graph.base_graph_store.GraphStore(Protocol)
```

图向量存储协议，定义图存储的通用接口（数据写入、查询、删除、混合检索、BFS 图扩展等）。所有图存储实现均需满足该协议。

**属性**：

- **config** -> GraphConfig：图存储配置。
- **semophore** -> asyncio.Semaphore | None：图存储信号量（用于并发控制）。
- **embedder** -> Embedding | None：绑定的嵌入模型。
- **return_similarity_score** -> bool：返回分数是否为相似度（True）而非距离。

### classmethod from_config

```python
@classmethod
def from_config(cls, config: GraphConfig, **kwargs) -> GraphStore
```

从配置创建图存储实例。

**参数**：

- **config**(GraphConfig)：图配置对象。
- **\*\*kwargs**：额外参数，传递给具体实现。

**返回**：

- **GraphStore**：配置好的图存储实例。

### rebuild

```python
def rebuild()
```

删除现有集合并重建索引（会清空数据）。

### async refresh

```python
async def refresh(skip_compact: bool = True, **kwargs)
```

刷新：将内存中的变更刷入数据库，并可选择是否进行 compact。

**参数**：

- **skip_compact**(bool, 可选)：是否跳过 compact。默认值：True。
- **\*\*kwargs**：其它刷新参数。

### async add_data

```python
async def add_data(collection: str, data: Iterable[dict], flush: bool = True, upsert: bool = False, **kwargs)
```

向指定集合写入任意字典数据。

**参数**：

- **collection**(str)：集合名称。
- **data**(Iterable[dict])：要插入的数据，可为 list、tuple 等可迭代对象。
- **flush**(bool, 可选)：是否立即刷盘。默认值：True。
- **upsert**(bool, 可选)：是否以 upsert 方式（存在则更新，否则插入）。默认值：False。
- **\*\*kwargs**：其它写入参数。

### async add_entity

```python
async def add_entity(entities: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False)
```

向图存储添加实体。除 `no_embed=True` 外会为实体生成嵌入。

**参数**：

- **entities**(Iterable)：要添加的 Entity 对象可迭代（如 list）。
- **flush**(bool, 可选)：是否立即刷盘。默认值：True。
- **upsert**(bool, 可选)：是否 upsert。默认值：False。
- **no_embed**(bool, 可选)：是否跳过嵌入。默认值：False。

### async add_relation

```python
async def add_relation(relations: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False)
```

向图存储添加关系。除 `no_embed=True` 外会为关系生成嵌入。

**参数**：

- **relations**(Iterable)：要添加的 Relation 对象可迭代。
- **flush**(bool, 可选)：是否立即刷盘。默认值：True。
- **upsert**(bool, 可选)：是否 upsert。默认值：False。
- **no_embed**(bool, 可选)：是否跳过嵌入。默认值：False。

### async add_episode

```python
async def add_episode(episodes: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False)
```

向图存储添加情节。除 `no_embed=True` 外会为情节生成嵌入。

**参数**：

- **episodes**(Iterable)：要添加的 Episode 对象可迭代。
- **flush**(bool, 可选)：是否立即刷盘。默认值：True。
- **upsert**(bool, 可选)：是否 upsert。默认值：False。
- **no_embed**(bool, 可选)：是否跳过嵌入。默认值：False。

### is_empty

```python
def is_empty(collection: str) -> bool
```

判断指定集合是否为空。

**参数**：

- **collection**(str)：集合名称。

**返回**：

- **bool**：集合为空返回 True，否则 False。

### async query

```python
async def query(
    collection: str,
    ids: list | None = None,
    expr: QueryExpr | None = None,
    silence_errors: bool = False,
    **kwargs,
) -> list[dict]
```

按集合查询图对象，支持按 id 列表或过滤表达式。

**参数**：

- **collection**(str)：集合名称。
- **ids**(list | None, 可选)：要查询的 id 列表。默认值：None。
- **expr**(QueryExpr | None, 可选)：过滤表达式。默认值：None。更多关于 QueryExpr 的配置选项，请参考 [QueryExpr 文档](../query/base.md)。
- **silence_errors**(bool, 可选)：是否抑制异常并返回空列表。默认值：False。
- **\*\*kwargs**：其它查询参数（如 limit、output_fields）。

**返回**：

- **list[dict]**：查询结果列表。

### async delete

```python
async def delete(
    collection: str,
    ids: list | None = None,
    expr: QueryExpr | None = None,
    **kwargs,
) -> dict
```

从集合中删除图对象，支持按 id 列表或过滤表达式。

**参数**：

- **collection**(str)：集合名称。
- **ids**(list | None, 可选)：要删除的 id 列表。默认值：None。
- **expr**(QueryExpr | None, 可选)：过滤表达式。默认值：None。更多关于 QueryExpr 的配置选项，请参考 [QueryExpr 文档](../query/base.md)。
- **\*\*kwargs**：其它删除参数。

**返回**：

- **dict**：删除操作结果。

### async search

```python
async def search(
    query: str,
    k: int,
    collection: str,
    ranker_config: BaseRankConfig,
    *,
    reranker: Reranker | None = None,
    bfs_depth: int = 0,
    bfs_k: int = 0,
    filter_expr: QueryExpr | None = None,
    output_fields: list[str] | None = None,
    query_embedding: list[float] | None = None,
    **kwargs,
) -> dict[str, list[dict]]
```

混合检索：对指定集合进行语义+稀疏检索，并可选用 Reranker、BFS 图扩展。

**参数**：

- **query**(str)：查询文本。
- **k**(int)：返回条数。
- **collection**(str)：集合名（如 `"ENTITY_COLLECTION"`、`"RELATION_COLLECTION"`、`"EPISODE_COLLECTION"` 或 `"all"`）。
- **ranker_config**(BaseRankConfig)：排序配置（如 [WeightedRankConfig](./config.md)）。
- **reranker**(Reranker | None, 可选)：交叉编码 Reranker。默认值：None。
- **bfs_depth**(int, 可选)：BFS 图扩展深度，0 表示不扩展。默认值：0。
- **bfs_k**(int, 可选)：BFS 每层扩展的最大节点数。默认值：0。
- **filter_expr**(QueryExpr | None, 可选)：过滤表达式。默认值：None。
- **output_fields**(list[str] | None, 可选)：返回字段列表。默认值：None。
- **query_embedding**(list[float] | None, 可选)：预计算的查询向量。默认值：None。
- **\*\*kwargs**：支持 `language`（rerank 语言，如 "cn"/"en"）、`min_score`（最低分数阈值）等。

**返回**：

- **dict[str, list[dict]]**：集合名到结果列表的映射。

### attach_embedder

```python
def attach_embedder(embedder: Embedding) -> None
```

绑定嵌入模型，用于 add_entity/add_relation/add_episode 与 search 时的向量化。

**参数**：

- **embedder**(Embedding)：嵌入模型实例。

### close

```python
def close() -> None
```

关闭后端连接并释放资源。

---

## class GraphStoreFactory

```python
class openjiuwen.core.foundation.store.graph.base.GraphStoreFactory
```

图存储工厂：根据配置创建对应后端的 GraphStore 实例，支持注册自定义后端。不可实例化。

### classmethod register_backend

```python
@classmethod
def register_backend(cls, name: str, backend: Type[GraphStore], force: bool = False)
```

注册图存储后端。

**参数**：

- **name**(str)：后端名称（如 `"milvus"`）。
- **backend**(Type[GraphStore])：实现 GraphStore 协议的类。
- **force**(bool, 可选)：是否覆盖已存在的同名后端。默认值：False。

### classmethod from_config

```python
@classmethod
def from_config(
    cls,
    config: GraphConfig,
    backend_name: str | None = None,
    **kwargs,
) -> GraphStore
```

根据配置创建 GraphStore 实例。若未指定 `backend_name`，使用 `config.backend`（默认 `"milvus"`）；若为 `"milvus"` 且尚未注册，会先注册 Milvus 支持再创建。

**参数**：

- **config**(GraphConfig)：图存储配置。
- **backend_name**(str | None, 可选)：覆盖配置中的后端名。默认值：None。
- **\*\*kwargs**：额外参数，传给具体实现。

**返回**：

- **GraphStore**：图存储实例。

---

## class MilvusGraphStore

```python
class openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusGraphStore(GraphStore)
```

基于 Milvus 的 GraphStore 实现，支持实体/关系/情节的向量与 BM25 混合检索、Reranker 重排及 BFS 图扩展。适用于 Milvus 单机/分布式部署。

```python
MilvusGraphStore(config: GraphConfig)
```

**参数**：

- **config**(GraphConfig)：图存储配置，其中 `db_embed_config.index_type` 需为 Milvus 向量字段类型（如 [MilvusAUTO](../vector_fields/milvus_fields.md)）。更多关于 VectorField 的配置选项，请参考 [MilvusVectorField 文档](../vector_fields/milvus_fields.md)。

### classmethod from_config

```python
@classmethod
def from_config(cls, config: GraphConfig, **kwargs) -> MilvusGraphStore
```

从配置创建 MilvusGraphStore 实例。

**参数**：

- **config**(GraphConfig)：图配置。
- **\*\*kwargs**：额外参数（当前实现中未使用）。

**返回**：

- **MilvusGraphStore**：配置好的实例。

### attach_embedder

```python
def attach_embedder(embedder: Embedding) -> None
```

绑定嵌入模型；若已绑定则忽略后续调用。

**参数**：

- **embedder**(Embedding)：嵌入模型实例。

### 使用示例

```python
from openjiuwen.core.foundation.store.graph import GraphStoreFactory
from openjiuwen.core.foundation.store.graph.config import GraphConfig, GraphStoreIndexConfig
from openjiuwen.core.foundation.store.graph.constants import ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO

config = GraphConfig(
    uri="http://localhost:19530",
    name="my_graph_db",
    embed_dim=256,
    db_embed_config=GraphStoreIndexConfig(
        index_type=MilvusAUTO(),
        distance_metric="cosine",
    ),
)
# 可选：config.embedding_model = embedder
store = GraphStoreFactory.from_config(config)
store.rebuild()

# 添加实体、关系、情节后检索
# await store.add_entity(entities, flush=True)
# await store.add_relation(relations, flush=True)
# await store.add_episode(episodes, flush=True)
# results = await store.search("query", k=5, collection=ENTITY_COLLECTION, ranker_config=WeightedRankConfig(...))

store.close()
```

更多完整示例见 [README](./README.md) 中的参考示例链接。
