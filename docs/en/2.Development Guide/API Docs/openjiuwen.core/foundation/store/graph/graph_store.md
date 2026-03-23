# openjiuwen.core.foundation.store.graph.base_graph_store / base / milvus

Graph vector store protocol and Milvus implementation: GraphStore interface, GraphStoreFactory, and MilvusGraphStore.

## class GraphStore

```python
class openjiuwen.core.foundation.store.graph.base_graph_store.GraphStore(Protocol)
```

Protocol for graph vector stores: data write, query, delete, hybrid search, and BFS graph expansion. All graph store implementations must satisfy this protocol.

**Properties**:

- **config** -> GraphConfig: Graph store configuration.
- **semophore** -> asyncio.Semaphore | None: Semaphore for concurrency control.
- **embedder** -> Embedding | None: Attached embedding model.
- **return_similarity_score** -> bool: Whether returned scores are similarity (True) rather than distance.

### classmethod from_config

```python
@classmethod
def from_config(cls, config: GraphConfig, **kwargs) -> GraphStore
```

Create a graph store instance from configuration.

**Parameters**:

- **config**(GraphConfig): Graph configuration object.
- **\*\*kwargs**: Extra arguments passed to the implementation.

**Returns**:

- **GraphStore**: Configured graph store instance.

### rebuild

```python
def rebuild()
```

Drop existing collections and rebuild indices (data is cleared).

### async refresh

```python
async def refresh(skip_compact: bool = True, **kwargs)
```

Flush in-memory changes to the database and optionally run compact.

**Parameters**:

- **skip_compact**(bool, optional): Whether to skip compact. Default: True.
- **\*\*kwargs**: Other refresh options.

### async add_data

```python
async def add_data(collection: str, data: Iterable[dict], flush: bool = True, upsert: bool = False, **kwargs)
```

Insert arbitrary dict data into a collection.

**Parameters**:

- **collection**(str): Collection name.
- **data**(Iterable[dict]): Data to insert (e.g. list or tuple).
- **flush**(bool, optional): Flush immediately. Default: True.
- **upsert**(bool, optional): Upsert (update if exists, insert otherwise). Default: False.
- **\*\*kwargs**: Other write options.

### async add_entity

```python
async def add_entity(entities: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False)
```

Add entities to the graph store. Embeddings are created unless `no_embed=True`.

**Parameters**:

- **entities**(Iterable): Iterable of Entity objects.
- **flush**(bool, optional): Flush immediately. Default: True.
- **upsert**(bool, optional): Upsert. Default: False.
- **no_embed**(bool, optional): Skip embedding. Default: False.

### async add_relation

```python
async def add_relation(relations: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False)
```

Add relations to the graph store. Embeddings are created unless `no_embed=True`.

**Parameters**:

- **relations**(Iterable): Iterable of Relation objects.
- **flush**(bool, optional): Flush immediately. Default: True.
- **upsert**(bool, optional): Upsert. Default: False.
- **no_embed**(bool, optional): Skip embedding. Default: False.

### async add_episode

```python
async def add_episode(episodes: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False)
```

Add episodes to the graph store. Embeddings are created unless `no_embed=True`.

**Parameters**:

- **episodes**(Iterable): Iterable of Episode objects.
- **flush**(bool, optional): Flush immediately. Default: True.
- **upsert**(bool, optional): Upsert. Default: False.
- **no_embed**(bool, optional): Skip embedding. Default: False.

### is_empty

```python
def is_empty(collection: str) -> bool
```

Check whether a collection is empty.

**Parameters**:

- **collection**(str): Collection name.

**Returns**:

- **bool**: True if empty, False otherwise.

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

Query graph objects by collection, with optional id list or filter expression.

**Parameters**:

- **collection**(str): Collection name.
- **ids**(list | None, optional): List of ids to query. Default: None.
- **expr**(QueryExpr | None, optional): Filter expression. Default: None. For QueryExpr options, see [QueryExpr documentation](../query/base.md).
- **silence_errors**(bool, optional): Suppress exceptions and return empty list. Default: False.
- **\*\*kwargs**: Other query options (e.g. limit, output_fields).

**Returns**:

- **list[dict]**: Query results.

### async delete

```python
async def delete(
    collection: str,
    ids: list | None = None,
    expr: QueryExpr | None = None,
    **kwargs,
) -> dict
```

Delete graph objects from a collection by id list or filter expression.

**Parameters**:

- **collection**(str): Collection name.
- **ids**(list | None, optional): List of ids to delete. Default: None.
- **expr**(QueryExpr | None, optional): Filter expression. Default: None. For QueryExpr options, see [QueryExpr documentation](../query/base.md).
- **\*\*kwargs**: Other delete options.

**Returns**:

- **dict**: Delete operation result.

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

Hybrid search: semantic + sparse search on a collection, with optional reranker and BFS graph expansion.

**Parameters**:

- **query**(str): Query text.
- **k**(int): Number of results to return.
- **collection**(str): Collection name (e.g. `"ENTITY_COLLECTION"`, `"RELATION_COLLECTION"`, `"EPISODE_COLLECTION"`, or `"all"`).
- **ranker_config**(BaseRankConfig): Ranking config (e.g. [WeightedRankConfig](./config.md)).
- **reranker**(Reranker | None, optional): Cross-encoder reranker. Default: None.
- **bfs_depth**(int, optional): BFS expansion depth; 0 means no expansion. Default: 0.
- **bfs_k**(int, optional): Max nodes to expand per BFS layer. Default: 0.
- **filter_expr**(QueryExpr | None, optional): Filter expression. Default: None.
- **output_fields**(list[str] | None, optional): Fields to return. Default: None.
- **query_embedding**(list[float] | None, optional): Precomputed query embedding. Default: None.
- **\*\*kwargs**: Supports `language` (e.g. "cn"/"en" for rerank), `min_score`, etc.

**Returns**:

- **dict[str, list[dict]]**: Map from collection name to result lists.

### attach_embedder

```python
def attach_embedder(embedder: Embedding) -> None
```

Attach an embedding model for add_entity/add_relation/add_episode and search.

**Parameters**:

- **embedder**(Embedding): Embedding model instance.

### close

```python
def close() -> None
```

Close the backend connection and release resources.

---

## class GraphStoreFactory

```python
class openjiuwen.core.foundation.store.graph.base.GraphStoreFactory
```

Factory for creating GraphStore instances from config; supports registering custom backends. Not instantiable.

### classmethod register_backend

```python
@classmethod
def register_backend(cls, name: str, backend: Type[GraphStore], force: bool = False)
```

Register a graph store backend.

**Parameters**:

- **name**(str): Backend name (e.g. `"milvus"`).
- **backend**(Type[GraphStore]): Class implementing the GraphStore protocol.
- **force**(bool, optional): Overwrite existing backend with the same name. Default: False.

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

Create a GraphStore instance from config. If `backend_name` is not set, `config.backend` is used (default `"milvus"`). For `"milvus"`, Milvus support is registered automatically if needed.

**Parameters**:

- **config**(GraphConfig): Graph store configuration.
- **backend_name**(str | None, optional): Override backend name from config. Default: None.
- **\*\*kwargs**: Extra arguments for the implementation.

**Returns**:

- **GraphStore**: Graph store instance.

---

## class MilvusGraphStore

```python
class openjiuwen.core.foundation.store.graph.milvus.milvus_support.MilvusGraphStore(GraphStore)
```

GraphStore implementation using Milvus: hybrid vector + BM25 search for entities, relations, and episodes, with optional reranker and BFS graph expansion.

```python
MilvusGraphStore(config: GraphConfig)
```

**Parameters**:

- **config**(GraphConfig): Graph store config; `db_embed_config.index_type` must be a Milvus vector field type (e.g. [MilvusAUTO](../vector_fields/milvus_fields.md)). For more VectorField options, see [MilvusVectorField documentation](../vector_fields/milvus_fields.md).

### classmethod from_config

```python
@classmethod
def from_config(cls, config: GraphConfig, **kwargs) -> MilvusGraphStore
```

Create a MilvusGraphStore instance from configuration.

**Parameters**:

- **config**(GraphConfig): Graph configuration.
- **\*\*kwargs**: Extra arguments (currently unused).

**Returns**:

- **MilvusGraphStore**: Configured instance.

### attach_embedder

```python
def attach_embedder(embedder: Embedding) -> None
```

Attach an embedding model; ignored if one is already attached.

**Parameters**:

- **embedder**(Embedding): Embedding model instance.

### Example

```python
from openjiuwen.core.foundation.store.graph import GraphStoreFactory
from openjiuwen.core.foundation.store.graph.config import GraphConfig, GraphStoreIndexConfig
from openjiuwen.core.foundation.store.graph.constants import ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION
from openjiuwen.core.foundation.store.graph.result_ranking import WeightedRankConfig
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
# Optional: config.embedding_model = embedder
store = GraphStoreFactory.from_config(config)
store.rebuild()

# After adding entities, relations, episodes:
# await store.add_entity(entities, flush=True)
# await store.add_relation(relations, flush=True)
# await store.add_episode(episodes, flush=True)
# results = await store.search("query", k=5, collection=ENTITY_COLLECTION, ranker_config=WeightedRankConfig(...))

store.close()
```

See the [README](./README.md) for links to full examples.
